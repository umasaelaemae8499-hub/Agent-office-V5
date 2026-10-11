import asyncio
import json

import httpx
import pytest

from app.agents.director import RuleBasedPlanner, parse_aspect, parse_duration, parse_style
from app.agents.schemas import Storyboard
from app.agents.storyboard import build_storyboard
from app.providers.base import (Capabilities, GenerationRequest, GenerationResult, ProviderError, ProviderStatus,
                                VideoProvider)
from app.providers.local_provider import ComfyUIProvider, substitute
from app.providers.router import ProviderRouter

KEYBOARD = ("Create a 30-second cinematic commercial showing a futuristic mechanical keyboard assembling itself, "
            "with floating components, realistic lighting, professional sound effects, smooth transitions, and "
            "premium product-advertising aesthetics. Export as a vertical 9:16 video.")
MICRO = ("Create a 45-second cinematic documentary about microplastics entering the human body, using realistic "
         "scientific visuals, dramatic lighting, professional narration, and subtle background music.")


def test_parsers():
    assert parse_duration("a 2 minute film") == 120
    assert parse_duration("45-second clip") == 45
    assert parse_aspect("for TikTok") == "9:16" and parse_aspect("square post") == "1:1"
    assert parse_style(MICRO) == "documentary"


@pytest.mark.parametrize("req,dur,aspect", [(KEYBOARD, 30, "9:16"), (MICRO, 45, "16:9")])
def test_plan_and_storyboard(req, dur, aspect):
    plan = RuleBasedPlanner().plan(req)
    assert plan.duration_seconds == dur and plan.aspect_ratio == aspect
    sb = build_storyboard(plan)
    rendered = sum(s.duration for s in sb.scenes) - sum(s.transition_duration for s in sb.scenes[1:])
    assert rendered == pytest.approx(dur, abs=0.05)
    assert len(sb.scenes) == plan.scene_count
    assert all(plan.style_spec.subject_identity in s.visual_prompt for s in sb.scenes)
    Storyboard.model_validate(json.loads(sb.model_dump_json()))  # round-trips


def test_plan_flags_audio():
    assert RuleBasedPlanner().plan(MICRO).audio.narration
    assert RuleBasedPlanner().plan(KEYBOARD).audio.sound_effects


def test_storyboard_rejects_bad_durations():
    plan = RuleBasedPlanner().plan(KEYBOARD)
    data = json.loads(build_storyboard(plan).model_dump_json())
    data["scenes"][0]["duration"] += 5
    with pytest.raises(ValueError):
        Storyboard.model_validate(data)


def test_substitute_keeps_types():
    wf = {"1": {"inputs": {"text": "{{PROMPT}}", "width": "{{WIDTH}}", "note": "seed={{SEED}}"}}}
    out = substitute(wf, {"PROMPT": "a cat", "WIDTH": 512, "SEED": 7})
    assert out["1"]["inputs"] == {"text": "a cat", "width": 512, "note": "seed=7"}


def _fake_comfy(tmp_path, fail_first=False):
    """In-process ComfyUI protocol double (tests the adapter's HTTP handling, NOT real generation)."""
    state = {"posts": 0}
    video_bytes = (tmp_path / "gen_src.mp4").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {}})
        if request.url.path == "/prompt":
            state["posts"] += 1
            body = json.loads(request.content)
            assert body["prompt"]["3"]["inputs"]["width"] == 320
            state["prompt"] = body["prompt"]["6"]["inputs"]["text"]
            return httpx.Response(200, json={"prompt_id": "abc12345"})
        if request.url.path.startswith("/history/"):
            return httpx.Response(200, json={"abc12345": {"status": {"completed": True, "status_str": "success"},
                                                          "outputs": {"9": {"gifs": [{"filename": "out.mp4",
                                                                                      "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=video_bytes)
        return httpx.Response(404)

    return httpx.MockTransport(handler), state


@pytest.fixture
def comfy_workflow(tmp_path, media_dir):
    (tmp_path / "gen_src.mp4").write_bytes((media_dir / "a.mp4").read_bytes())
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps({"3": {"inputs": {"width": "{{WIDTH}}", "height": "{{HEIGHT}}", "length": "{{FRAMES}}"}},
                              "6": {"inputs": {"text": "{{PROMPT}}"}}}))
    return wf


def test_comfyui_adapter_protocol(tmp_path, comfy_workflow):
    transport, state = _fake_comfy(tmp_path)
    p = ComfyUIProvider("http://comfy.test", comfy_workflow, None, poll_interval=0.01, transport=transport)
    assert asyncio.run(p.check_available()).available
    res = asyncio.run(p.generate(GenerationRequest(prompt="a keyboard", width=320, height=180), tmp_path / "o"))
    assert res.status == "succeeded" and res.cost_usd == 0
    assert (tmp_path / "o").exists() and state["prompt"] == "a keyboard"


def test_comfyui_unreachable_reports_unavailable(tmp_path, comfy_workflow):
    def down(request):
        raise httpx.ConnectError("refused")
    p = ComfyUIProvider("http://comfy.test", comfy_workflow, None, transport=httpx.MockTransport(down))
    st = asyncio.run(p.check_available())
    assert not st.available and "not reachable" in st.reason


class FakePaid(VideoProvider):
    name, is_paid, verified = "fakepaid", True, True

    def __init__(self, path=None):
        self.calls = 0
        self.path = path

    async def check_available(self):
        return ProviderStatus(True)

    def capabilities(self):
        return Capabilities(text_to_video=True, max_duration=10)

    def estimate_cost(self, req):
        return 0.5 * req.duration

    async def generate(self, req, out_dir):
        self.calls += 1
        return GenerationResult(self.name, "succeeded", self.path, cost_usd=self.estimate_cost(req))


def test_router_never_spends_without_permission(settings, store):
    pid = store.create_project("x", "text_to_video", budget_usd=100)
    router = ProviderRouter(settings, store, providers=[FakePaid()])
    d = asyncio.run(router.route(pid, GenerationRequest(prompt="p", duration=5), "hybrid"))
    assert d.provider is None  # ALLOW_PAID_GENERATION defaults to false
    settings.budget.allow_paid_generation = True
    settings.budget.max_project_budget_usd = 10
    d = asyncio.run(router.route(pid, GenerationRequest(prompt="p", duration=5), "hybrid"))
    assert d.provider is None and d.requires_approval and d.estimated_cost == 2.5  # not approved yet
    store.update_project(pid, paid_approved=1)
    d = asyncio.run(router.route(pid, GenerationRequest(prompt="p", duration=5), "hybrid"))
    assert d.provider is not None
    d = asyncio.run(router.route(pid, GenerationRequest(prompt="p", duration=30), "hybrid"))
    assert d.provider is None and "remaining budget" in d.reason  # $15 > $10 cap
    assert asyncio.run(router.route(pid, GenerationRequest(prompt="p", duration=5), "local")).provider is None


def test_router_reuses_cache_and_bounds_retries(settings, store, media_dir):
    pid = store.create_project("x", "text_to_video")

    class Flaky(FakePaid):
        name, is_paid = "flaky", False

        async def generate(self, req, out_dir):
            self.calls += 1
            raise ProviderError("temporary", retryable=True)

    flaky = Flaky()
    router = ProviderRouter(settings, store, providers=[flaky])
    import app.providers.router as r
    orig = r.asyncio.sleep
    r.asyncio.sleep = lambda s: orig(0)
    try:
        res = asyncio.run(router.run_job(pid, 1, flaky, GenerationRequest(prompt="p"), settings.cache_dir))
    finally:
        r.asyncio.sleep = orig
    assert res.status == "failed" and flaky.calls == settings.execution.max_retries + 1

    good = FakePaid(str(media_dir / "a.mp4"))
    good.is_paid = False
    router = ProviderRouter(settings, store, providers=[good])
    req = GenerationRequest(prompt="cached prompt")
    asyncio.run(router.run_job(pid, 1, good, req, settings.cache_dir))
    d = asyncio.run(router.route(pid, req, "local"))
    assert d.cached_path == str(media_dir / "a.mp4")
