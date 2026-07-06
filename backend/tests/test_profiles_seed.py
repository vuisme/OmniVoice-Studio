from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

_TMP = tempfile.mkdtemp(prefix="omnivoice_profiles_seed_test_")
_config = types.ModuleType("core.config")
_config.DATA_DIR = _TMP
_config.VOICES_DIR = str(Path(_TMP) / "voices")
_config.OUTPUTS_DIR = str(Path(_TMP) / "outputs")
_config.DB_PATH = str(Path(_TMP) / "omnivoice.db")
Path(_config.VOICES_DIR).mkdir(parents=True, exist_ok=True)
sys.modules["core.config"] = _config

_omnivoice = types.ModuleType("omnivoice")
_omnivoice_utils = types.ModuleType("omnivoice.utils")
_voice_design = types.ModuleType("omnivoice.utils.voice_design")
_voice_design.heal_design_instruct = lambda instruct, parsed: instruct
_voice_design.sanitize_instruct = lambda instruct: instruct
sys.modules["omnivoice"] = _omnivoice
sys.modules["omnivoice.utils"] = _omnivoice_utils
sys.modules["omnivoice.utils.voice_design"] = _voice_design

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.db import init_db  # noqa: E402
from api.routers import profiles as profiles_router  # noqa: E402
from services import vietnamese_voice_seed  # noqa: E402

init_db()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(profiles_router.router)
    return TestClient(app)


def test_seed_vietnamese_profiles_starts_background_import(monkeypatch):
    monkeypatch.setattr(vietnamese_voice_seed, "vietnamese_voices_present", lambda: False)
    monkeypatch.setattr(
        vietnamese_voice_seed,
        "seed_vietnamese_voices_background",
        lambda *, cooldown_s=0: True,
    )

    res = _client().post("/profiles/seed/vietnamese")

    assert res.status_code == 200
    assert res.json() == {
        "present": False,
        "started": True,
        "source": "STBack23/omnivoice-vi",
    }


def test_seed_vietnamese_profiles_noops_when_present(monkeypatch):
    called = False

    def _start(*, cooldown_s=0):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(vietnamese_voice_seed, "vietnamese_voices_present", lambda: True)
    monkeypatch.setattr(vietnamese_voice_seed, "seed_vietnamese_voices_background", _start)

    res = _client().post("/profiles/seed/vietnamese")

    assert res.status_code == 200
    assert res.json()["present"] is True
    assert res.json()["started"] is False
    assert called is False
