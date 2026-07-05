from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from core.db import db_conn

SESSION_COOKIE = "mlac_session"
STATE_COOKIE = "mlac_oauth_state"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def google_client_secret() -> str:
    return os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def auth_public_base_url() -> str:
    raw = (
        os.environ.get("MLAC_AUTH_PUBLIC_BASE_URL")
        or os.environ.get("OMNIVOICE_AUTH_PUBLIC_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def auth_enabled() -> bool:
    raw = os.environ.get("MLAC_AUTH_ENABLED") or os.environ.get("OMNIVOICE_AUTH_ENABLED")
    if raw is not None:
        return _truthy(raw)
    return bool(google_client_id() and google_client_secret())


def _session_secret() -> bytes:
    configured = (
        os.environ.get("MLAC_AUTH_SESSION_SECRET")
        or os.environ.get("OMNIVOICE_AUTH_SESSION_SECRET")
        or ""
    ).strip()
    if configured:
        return configured.encode("utf-8")
    # Dev fallback: survives within this process only. Production should set env.
    if not hasattr(_session_secret, "_generated"):
        setattr(_session_secret, "_generated", secrets.token_urlsafe(48))
    return getattr(_session_secret, "_generated").encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_session(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def verify_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = _b64(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except Exception:
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def make_session(email: str, *, name: str = "", picture: str = "", admin: bool = False) -> str:
    now = int(time.time())
    ttl_days = int(os.environ.get("MLAC_AUTH_SESSION_DAYS", "14") or "14")
    return sign_session(
        {
            "email": email.lower(),
            "name": name,
            "picture": picture,
            "admin": bool(admin),
            "iat": now,
            "exp": now + max(1, ttl_days) * 86400,
        }
    )


def _split_emails(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({p.strip().lower() for p in value.replace(";", ",").split(",") if p.strip()})


def seed_auth_users_from_env() -> None:
    active = _split_emails(
        os.environ.get("MLAC_AUTH_ALLOWED_EMAILS") or os.environ.get("OMNIVOICE_AUTH_ALLOWED_EMAILS")
    )
    admins = _split_emails(
        os.environ.get("MLAC_AUTH_ADMIN_EMAILS") or os.environ.get("OMNIVOICE_AUTH_ADMIN_EMAILS")
    )
    if not active and not admins:
        return
    now = time.time()
    with db_conn() as conn:
        for email in sorted(set(active + admins)):
            conn.execute(
                """
                INSERT INTO auth_users(email, active, admin, created_at, updated_at)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    active=1,
                    admin=max(auth_users.admin, excluded.admin),
                    updated_at=excluded.updated_at
                """,
                (email, 1 if email in admins else 0, now, now),
            )


@dataclass
class AuthUser:
    email: str
    active: bool
    admin: bool
    name: str = ""
    picture: str = ""
    google_sub: str = ""


def get_user(email: str) -> AuthUser | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT email, active, admin, name, picture, google_sub FROM auth_users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
    if not row:
        return None
    return AuthUser(
        email=row["email"],
        active=bool(row["active"]),
        admin=bool(row["admin"]),
        name=row["name"] or "",
        picture=row["picture"] or "",
        google_sub=row["google_sub"] or "",
    )


def list_users() -> list[dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT email, active, admin, name, picture, google_sub, last_login_at, created_at, updated_at
            FROM auth_users
            ORDER BY email
            """
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_user(email: str, *, active: bool, admin: bool = False) -> dict[str, Any]:
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("valid email required")
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO auth_users(email, active, admin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                active=excluded.active,
                admin=excluded.admin,
                updated_at=excluded.updated_at
            """,
            (normalized, int(active), int(admin), now, now),
        )
    return get_user(normalized).__dict__


def delete_user(email: str) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM auth_users WHERE email = ?", (email.strip().lower(),))


def mark_login(email: str, profile: dict[str, Any]) -> AuthUser | None:
    normalized = email.lower()
    user = get_user(normalized)
    if not user or not user.active:
        return user
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE auth_users
            SET name=?, picture=?, google_sub=?, last_login_at=?, updated_at=?
            WHERE email=?
            """,
            (
                profile.get("name") or "",
                profile.get("picture") or "",
                profile.get("sub") or "",
                now,
                now,
                normalized,
            ),
        )
    return get_user(normalized)


def build_google_auth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": google_client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_google_code(code: str, redirect_uri: str) -> dict[str, Any]:
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": google_client_id(),
            "client_secret": google_client_secret(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_google_id_token(id_token: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"id_token": id_token})
    with urllib.request.urlopen(f"{GOOGLE_TOKENINFO_URL}?{qs}", timeout=15) as resp:
        profile = json.loads(resp.read().decode("utf-8"))
    if profile.get("aud") != google_client_id():
        raise ValueError("Google token audience mismatch")
    if profile.get("email_verified") not in (True, "true", "True", "1", 1):
        raise ValueError("Google email is not verified")
    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google profile has no email")
    profile["email"] = email
    return profile
