"""Pipeline integration tests. The text-to-video test uses a fake provider that returns
local test footage - it verifies pipeline wiring only and is NOT a real generation test."""

from pathlib import Path

from app.pipeline.orchestrator import Orchestrator
from app.providers.router import ProviderRouter
from app.utils.ffmpeg import probe
from tests.test_planning_providers import FakePaid


def test_video_to_video_end_to_end_and_resume(settings, store, media_dir):
    orch = Orchestrator(settings, store)
    pid = orch.create_project("Create a 10-second fast-paced montage, square", "video_to_video",
                              [media_dir / "a.mp4", media_dir / "b.mp4", media_dir / "black.mp4"],
                              overrides={"aspect_ratio": "1:1"}, music=media_dir / "music.wav")
    res = orch.run(pid)
    assert res["status"] == "completed", res
    out = Path(res["output"])
    info = probe(out)
    assert (info.width, info.height) == (1080, 1080)
    assert abs(info.duration - 10) < 0.1
    assert store.load_doc(pid, "qc_report")["passed"]
    assert (store.project_dir(pid) / "production_report.md").exists()
    assert not (store.project_dir(pid) / "tmp").exists()  # temp cleaned after validation
    mtime = out.stat().st_mtime
    calls = []
    res2 = Orchestrator(settings, store, progress=lambda s, m, f: calls.append(m)).run(pid)
    assert res2["status"] == "completed" and out.stat().st_mtime == mtime  # nothing redone
    assert all("reused" in m for m in calls)
    res3 = Orchestrator(settings, store).run(pid, from_stage="render")  # explicit re-render
    assert res3["status"] == "completed" and out.stat().st_mtime > mtime


def test_text_to_video_blocks_without_provider(settings, store):
    orch = Orchestrator(settings, store, router=ProviderRouter(settings, store, providers=[]))
    pid = orch.create_project("Create a 10-second cinematic video about a lighthouse", "text_to_video")
    res = orch.run(pid)
    assert res["status"] == "blocked" and res["stage"] == "generate"
    assert "Text-to-Video unavailable" in res["reason"]
    assert store.require_project(pid)["output_path"] is None  # no fake output


def test_text_to_video_pipeline_wiring_with_fake_local_provider(settings, store, media_dir):
    fake = FakePaid(str(media_dir / "a.mp4"))
    fake.is_paid = False
    fake.estimate_cost = lambda req: 0.0
    orch = Orchestrator(settings, store, router=ProviderRouter(settings, store, providers=[fake]))
    pid = orch.create_project("Create a 12-second cinematic video about a lighthouse, square", "text_to_video",
                              overrides={"aspect_ratio": "1:1"})
    res = orch.run(pid)
    assert res["status"] == "completed", res
    assert abs(probe(res["output"]).duration - 12) < 0.1
    n_scenes = store.load_doc(pid, "plan")["scene_count"]
    assert fake.calls == n_scenes and len(store.jobs(pid)) == n_scenes
    # Resuming from 'generate' must reuse completed scenes, not regenerate them.
    res2 = orch.run(pid, from_stage="generate")
    assert res2["status"] == "completed" and fake.calls == n_scenes
    # A new project with the same storyboard hits the content-hash cache.
    store.save_doc(pid, "generation", {})
    orch.run(pid, from_stage="generate")
    assert fake.calls == n_scenes


import pytest  # noqa: E402

from app.audio.narration import get_tts_backend  # noqa: E402


@pytest.mark.skipif(get_tts_backend() is None, reason="no offline TTS engine installed")
def test_narrated_subtitled_edit(settings, store, media_dir):
    orch = Orchestrator(settings, store)
    pid = orch.create_project("Create a 9-second documentary with narration and subtitles, vertical",
                              "video_to_video", [media_dir / "a.mp4", media_dir / "b.mp4"],
                              overrides={"aspect_ratio": "9:16"},
                              narration_script="Plastic is everywhere. It is in the water we drink.")
    res = orch.run(pid)
    assert res["status"] == "completed", res
    tl = store.load_doc(pid, "timeline")
    assert len(tl["narration"]) == 2 and tl["subtitles"]["path"].endswith(".ass")
    assert "-->" in (store.project_dir(pid) / "subtitles" / "subtitles.srt").read_text()
