import json

import pytest

from app.config import load_settings
from app.utils.paths import UnsafePathError, atomic_write_json, ensure_within, sanitize_filename, slugify


def test_sanitize_filename_blocks_traversal_and_reserved():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\a.dll") == "a.dll"
    assert sanitize_filename('bad<>:"|?*name.mp4') == "bad_______name.mp4"
    assert sanitize_filename("CON.mp4") == "_CON.mp4"
    assert sanitize_filename("   ") == "file"


def test_ensure_within(tmp_path):
    assert ensure_within("sub/x.txt", tmp_path) == (tmp_path / "sub/x.txt").resolve()
    with pytest.raises(UnsafePathError):
        ensure_within("../outside.txt", tmp_path)
    with pytest.raises(UnsafePathError):
        ensure_within("/etc/passwd", tmp_path)


def test_atomic_write(tmp_path):
    p = tmp_path / "d" / "x.json"
    atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]


def test_slugify():
    assert slugify("Futuristic Keyboard!! 9:16") == "futuristic_keyboard_9_16"


def test_budget_defaults_forbid_paid(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_GENERATION", raising=False)
    monkeypatch.delenv("MAX_PROJECT_BUDGET_USD", raising=False)
    s = load_settings(root_dir=tmp_path)
    assert s.budget.allow_paid_generation is False
    assert s.budget.max_project_budget_usd == 0
    assert s.ui.host == "127.0.0.1"


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_PAID_GENERATION", "true")
    monkeypatch.setenv("MAX_PROJECT_BUDGET_USD", "3.5")
    monkeypatch.setenv("AUTOCINE_RENDER_CRF", "18")
    s = load_settings(root_dir=tmp_path)
    assert s.budget.allow_paid_generation is True
    assert s.budget.max_project_budget_usd == 3.5
    assert s.render.crf == 18


def test_store_lifecycle(store):
    pid = store.create_project("make a video", "video_to_video")
    assert store.get_project(pid)["status"] == "created"
    store.set_stage(pid, "plan", "done")
    store.set_stage(pid, "render", "done")
    assert store.stage_done(pid, "plan")
    store.reset_stages_from(pid, "render")
    assert store.stage_done(pid, "plan") and not store.stage_done(pid, "render")
    store.save_doc(pid, "plan", {"x": 1})
    assert store.load_doc(pid, "plan") == {"x": 1}
    with pytest.raises(ValueError):
        store.project_dir("../evil")
    with pytest.raises(ValueError):
        store.update_project(pid, user_request="sql injection attempt")


def test_doctor_runs(settings):
    from app.system.doctor import run_doctor
    r = run_doctor(settings)
    assert r.ffmpeg and "libx264" in r.h264_encoders_working
    assert r.features["existing_video_to_final_video"] == "available"
    assert "text_to_video" in r.features
