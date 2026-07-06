from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_TMP = tempfile.mkdtemp(prefix="omnivoice_gallery_seed_test_")
_config = types.ModuleType("core.config")
_config.DATA_DIR = _TMP
_config.VOICES_DIR = str(Path(_TMP) / "voices")
_config.OUTPUTS_DIR = str(Path(_TMP) / "outputs")
_config.DB_PATH = str(Path(_TMP) / "omnivoice.db")
Path(_config.VOICES_DIR).mkdir(parents=True, exist_ok=True)
Path(_config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
sys.modules["core.config"] = _config

_ffmpeg_utils = sys.modules.get("services.ffmpeg_utils")
if _ffmpeg_utils is not None and not hasattr(_ffmpeg_utils, "spawn_subprocess"):
    async def _spawn_subprocess_stub(*args, **kwargs):
        raise RuntimeError("spawn_subprocess is not used by gallery seed tests")

    _ffmpeg_utils.spawn_subprocess = _spawn_subprocess_stub

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db import db_conn, init_db  # noqa: E402
from api.routers import gallery  # noqa: E402

init_db()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(gallery.router)
    return TestClient(app)


def test_init_gallery_db_seeds_vietnamese_imports():
    gallery._init_gallery_db()

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, source_type, source_url FROM voice_gallery "
            "WHERE id LIKE 'omnivoice-vi-%' ORDER BY id",
        ).fetchall()

    assert len(rows) == 6
    assert {r["name"] for r in rows} >= {"Ban Mai", "Lan Trinh", "Ngân Hà"}
    assert all(r["source_type"] == "sample" for r in rows)
    assert all("STBack23/omnivoice-vi" in r["source_url"] for r in rows)


def test_seeded_gallery_voice_saves_as_profile_after_lazy_audio(monkeypatch):
    gallery._init_gallery_db()
    audio_path = Path(_config.OUTPUTS_DIR) / "voice_gallery" / "fake.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(gallery, "_ensure_gallery_audio", lambda row: str(audio_path))

    res = _client().post(
        "/gallery/voices/omnivoice-vi-ban-mai/save-as-profile",
        params={"profile_name": "Ban Mai"},
    )

    assert res.status_code == 200
    profile_id = res.json()["profile_id"]
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
    assert row is not None
    assert row["name"] == "Ban Mai"
    assert row["language"] == "Auto"
    assert row["instruct"] == ""
