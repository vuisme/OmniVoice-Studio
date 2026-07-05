from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core import event_bus
from core.config import VOICES_DIR
from core.db import db_conn

logger = logging.getLogger("omnivoice.vi_voices")

_REPO = "STBack23/omnivoice-vi"
_REVISION = "main"
_BASE = f"https://huggingface.co/datasets/{_REPO}/resolve/{_REVISION}"


@dataclass(frozen=True)
class VietnameseVoice:
    slug: str
    name: str
    audio_file: str

    @property
    def profile_id(self) -> str:
        return f"omnivoice-vi-{self.slug.replace('_', '-')}"

    @property
    def local_audio(self) -> str:
        ext = Path(self.audio_file).suffix or ".wav"
        return f"omnivoice_vi_{self.slug}{ext}"

    @property
    def local_voice_pt(self) -> str:
        return f"omnivoice_vi_{self.slug}.pt"


_VOICES = (
    VietnameseVoice("ban_mai", "Ban Mai", "ref.mp3"),
    VietnameseVoice("lan_trinh", "Lan Trinh", "ref.wav"),
    VietnameseVoice("ngan_ha", "Ngan Ha", "ref.wav"),
    VietnameseVoice("ngoc_huyen", "Ngoc Huyen", "ref.mp3"),
    VietnameseVoice("thao_trinh", "Thao Trinh", "ref.wav"),
    VietnameseVoice("tuong_vy", "Tuong Vy", "ref.wav"),
)


def _dataset_url(path: str) -> str:
    return f"{_BASE}/{urllib.parse.quote(path)}"


def _download(path: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return False
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(
        _dataset_url(path),
        headers={"User-Agent": "MiloAnCutlabs/voice-seed"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        tmp.write_bytes(resp.read())
    tmp.replace(dest)
    return True


def _download_text(path: str) -> str:
    req = urllib.request.Request(
        _dataset_url(path),
        headers={"User-Agent": "MiloAnCutlabs/voice-seed"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace").strip()


def _download_profile(slug: str) -> dict:
    try:
        raw = _download_text(f"voices/{slug}/profile.json")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def seed_vietnamese_voices() -> int:
    """Download and register the curated Vietnamese voice pack once.

    The files live in the user's data volume, not in git or the Docker image.
    The operation is idempotent: stable profile ids make repeated startup runs
    update missing assets without duplicating the voice list.
    """
    if os.environ.get("MLAC_SEED_VI_VOICES", "1").strip().lower() in {"0", "false", "no", "off"}:
        return 0

    voices_dir = Path(VOICES_DIR)
    voices_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    for voice in _VOICES:
        try:
            audio_path = voices_dir / voice.local_audio
            _download(f"voices/{voice.slug}/{voice.audio_file}", audio_path)
            # Keep the source prompt cache next to the reference clip for future
            # engines that can consume it. Current profile generation uses
            # ref_audio_path + ref_text and builds its own in-memory cache.
            _download(f"voices/{voice.slug}/voice.pt", voices_dir / voice.local_voice_pt)
            try:
                ref_text = _download_text(f"voices/{voice.slug}/ref_text.txt")
            except Exception:
                ref_text = ""
            source_profile = _download_profile(voice.slug)
            now = time.time()
            with db_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM voice_profiles WHERE id = ?",
                    (voice.profile_id,),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE voice_profiles
                        SET name=?, ref_audio_path=?, ref_text=?, language=?,
                            personality=?, kind=?, description=COALESCE(NULLIF(description, ''), ?)
                        WHERE id=?
                        """,
                        (
                            source_profile.get("name") or voice.name,
                            voice.local_audio,
                            ref_text,
                            "Vietnamese",
                            "omnivoice-vi",
                            "clone",
                            "Vietnamese sample voice from STBack23/omnivoice-vi.",
                            voice.profile_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO voice_profiles
                            (id, name, ref_audio_path, ref_text, instruct, language,
                             seed, personality, description, kind, created_at)
                        VALUES (?, ?, ?, ?, '', ?, NULL, ?, ?, 'clone', ?)
                        """,
                        (
                            voice.profile_id,
                            source_profile.get("name") or voice.name,
                            voice.local_audio,
                            ref_text,
                            "Vietnamese",
                            "omnivoice-vi",
                            "Vietnamese sample voice from STBack23/omnivoice-vi.",
                            now,
                        ),
                    )
                    created += 1
        except Exception as exc:
            logger.warning("Vietnamese voice seed skipped for %s: %s", voice.slug, exc)

    if created:
        event_bus.emit("profiles", {"action": "seeded", "source": "omnivoice-vi", "count": created})
        logger.info("Seeded %d Vietnamese voice profile(s) from %s.", created, _REPO)
    return created
