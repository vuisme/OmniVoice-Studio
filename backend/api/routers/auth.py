from __future__ import annotations

import secrets
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class UserPatch(BaseModel):
    email: str
    active: bool = True
    admin: bool = False


def _session_payload(request: Request) -> dict | None:
    return auth_service.verify_session(request.cookies.get(auth_service.SESSION_COOKIE))


def _require_admin(request: Request) -> dict:
    payload = _session_payload(request)
    if not payload:
        raise HTTPException(status_code=401, detail="login required")
    if not payload.get("admin"):
        raise HTTPException(status_code=403, detail="admin required")
    return payload


def _google_redirect_uri(request: Request) -> str:
    public_base = auth_service.auth_public_base_url()
    if public_base:
        return f"{public_base}/auth/google/callback"
    return str(request.url_for("google_callback"))


@router.get("/status")
def status(request: Request):
    payload = _session_payload(request)
    enabled = auth_service.auth_enabled()
    user = None
    if payload:
        db_user = auth_service.get_user(payload.get("email", ""))
        if db_user and db_user.active:
            user = {
                "email": db_user.email,
                "name": payload.get("name") or db_user.name,
                "picture": payload.get("picture") or db_user.picture,
                "admin": db_user.admin,
            }
    return {
        "enabled": enabled,
        "authenticated": bool(user) if enabled else True,
        "user": user,
        "google_configured": bool(auth_service.google_client_id() and auth_service.google_client_secret()),
    }


@router.get("/google/login")
def google_login(request: Request, return_to: str = "/"):
    if not auth_service.auth_enabled():
        return RedirectResponse(return_to or "/")
    if not auth_service.google_client_id() or not auth_service.google_client_secret():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    state = secrets.token_urlsafe(32)
    redirect_uri = _google_redirect_uri(request)
    url = auth_service.build_google_auth_url(redirect_uri, state)
    resp = RedirectResponse(url)
    resp.set_cookie(
        auth_service.STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=600,
        path="/",
    )
    resp.set_cookie(
        "mlac_return_to",
        return_to if return_to.startswith("/") else "/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=600,
        path="/",
    )
    return resp


@router.get("/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = ""):
    expected = request.cookies.get(auth_service.STATE_COOKIE) or ""
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code")

    redirect_uri = _google_redirect_uri(request)
    try:
        token = auth_service.exchange_google_code(code, redirect_uri)
        profile = auth_service.verify_google_id_token(token["id_token"])
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Google login failed: {e}") from e

    db_user = auth_service.mark_login(profile["email"], profile)
    if not db_user:
        return _denied(
            "Email is not on the activation list.",
            f"{profile['email']} is not allowed. Add it in the auth users list and set active=true.",
        )
    if not db_user.active:
        return _denied(
            "Account is not active.",
            f"{profile['email']} exists but is inactive. Ask an admin to activate it.",
        )

    session = auth_service.make_session(
        db_user.email,
        name=profile.get("name") or db_user.name,
        picture=profile.get("picture") or db_user.picture,
        admin=db_user.admin,
    )
    return_to = request.cookies.get("mlac_return_to") or "/"
    if not return_to.startswith("/"):
        return_to = "/"
    resp = RedirectResponse(return_to)
    resp.set_cookie(
        auth_service.SESSION_COOKIE,
        session,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=14 * 86400,
        path="/",
    )
    resp.delete_cookie(auth_service.STATE_COOKIE, path="/")
    resp.delete_cookie("mlac_return_to", path="/")
    return resp


def _denied(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Access denied</title></head>
<body style="font-family:system-ui;margin:48px;line-height:1.5">
<h1>{title}</h1><p>{body}</p><p><a href="/">Back to MiloAnCutlabs</a></p>
</body></html>""",
        status_code=403,
    )


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth_service.SESSION_COOKIE, path="/")
    return resp


@router.get("/users")
def users(request: Request):
    _require_admin(request)
    return {"users": auth_service.list_users()}


@router.post("/users")
def save_user(payload: UserPatch, request: Request):
    _require_admin(request)
    try:
        user = auth_service.upsert_user(payload.email, active=payload.active, admin=payload.admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"user": user}


@router.delete("/users/{email}")
def remove_user(email: str, request: Request):
    _require_admin(request)
    auth_service.delete_user(urllib.parse.unquote(email))
    return {"ok": True}
