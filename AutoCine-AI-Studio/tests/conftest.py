from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.database.storage import ProjectStore  # noqa: E402
from tests import media  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for var in ("MAX_PROJECT_BUDGET_USD", "ALLOW_PAID_GENERATION"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings(root_dir=tmp_path)
    s.render.preset = "ultrafast"
    s.execution.max_retries = 1
    s.ensure_dirs()
    return s


@pytest.fixture
def store(settings):
    return ProjectStore(settings.database_path, settings.projects_dir)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    media.make_clip(d / "a.mp4", 4, "640x360", pattern="testsrc2", tone_hz=440)
    media.make_clip(d / "b.mp4", 4, "360x640", pattern="smptehdbars", tone_hz=660)
    media.make_clip(d / "c.mp4", 3, "480x480", pattern="testsrc", tone_hz=None)
    media.make_black_clip(d / "black.mp4", 3)
    media.make_frozen_then_moving(d / "frozen.mp4")
    media.make_music(d / "music.wav", 20, 120)
    media.make_voice_like(d / "voice.wav", 2)
    media.make_image(d / "still.png", "512x512")
    return d
