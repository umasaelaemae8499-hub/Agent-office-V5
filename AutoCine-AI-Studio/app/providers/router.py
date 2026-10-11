"""Provider routing + budget enforcement + cached, retrying generation jobs.

Priority (hybrid mode): reuse cached asset -> local provider -> free provider ->
paid provider ONLY if paid generation is enabled, the project is approved, and the
estimated cost fits the remaining budget. Never spends money implicitly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.database.storage import ProjectStore
from app.providers.base import GenerationRequest, GenerationResult, ProviderError, VideoProvider
from app.providers.cloud_provider import UnverifiedCloudProvider
from app.providers.local_provider import ComfyUIProvider
from app.utils.paths import content_hash

log = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    provider: VideoProvider | None
    reason: str
    estimated_cost: float = 0.0
    requires_approval: bool = False
    cached_path: str | None = None


def build_providers(settings: Settings) -> list[VideoProvider]:
    providers: list[VideoProvider] = []
    cfg = settings.providers
    c = cfg.get("comfyui", {})
    if c.get("enabled", True):
        vw, iw = c.get("video_workflow"), c.get("image_workflow")
        providers.append(ComfyUIProvider(
            base_url=c.get("base_url", "http://127.0.0.1:8188"),
            video_workflow=settings.resolve(vw) if vw else None,
            image_workflow=settings.resolve(iw) if iw else None,
            timeout=float(c.get("timeout_seconds", 1800)), poll_interval=float(c.get("poll_interval_seconds", 3))))
    for name in ("kling", "runway"):
        pc = cfg.get(name, {})
        if pc:
            providers.append(UnverifiedCloudProvider(name, pc.get("api_key_env", "")))
    return providers


class ProviderRouter:
    def __init__(self, settings: Settings, store: ProjectStore, providers: list[VideoProvider] | None = None) -> None:
        self.settings = settings
        self.store = store
        self.providers = providers if providers is not None else build_providers(settings)
        self._status_cache: dict[str, tuple[bool, str]] = {}

    async def provider_status(self) -> dict[str, tuple[bool, str]]:
        for p in self.providers:
            if p.name not in self._status_cache:
                st = await p.check_available()
                self._status_cache[p.name] = (st.available, st.reason)
        return self._status_cache

    async def route(self, project_id: str, req: GenerationRequest, exec_mode: str) -> RouteDecision:
        # 1. cache
        for p in self.providers:
            hit = self.store.find_succeeded_job(content_hash(req.cache_key(p.name)))
            if hit and hit["output_path"] and Path(hit["output_path"]).exists():
                return RouteDecision(p, "cached result reused", cached_path=hit["output_path"])
        status = await self.provider_status()
        project = self.store.require_project(project_id)
        reasons = []

        def capable(p: VideoProvider) -> bool:
            caps = p.capabilities()
            return caps.text_to_video if req.kind == "video" else caps.text_to_image

        # 2./3. local + free providers
        if exec_mode in ("local", "hybrid"):
            for p in self.providers:
                ok, why = status[p.name]
                if not p.is_paid:
                    if ok and capable(p):
                        return RouteDecision(p, f"{p.name} (local/free)")
                    reasons.append(f"{p.name}: {why if not ok else 'no ' + req.kind + ' workflow'}")
        # 4./5. paid providers, budget-gated
        if exec_mode in ("cloud", "hybrid"):
            b = self.settings.budget
            for p in self.providers:
                if not p.is_paid:
                    continue
                ok, why = status[p.name]
                if not ok or not capable(p):
                    reasons.append(f"{p.name}: {why}")
                    continue
                cost = p.estimate_cost(req)
                remaining = min(b.max_project_budget_usd, project["budget_usd"] or b.max_project_budget_usd) \
                    - project["actual_cost_usd"]
                if not b.allow_paid_generation:
                    reasons.append(f"{p.name}: paid generation disabled (ALLOW_PAID_GENERATION=false)")
                    continue
                if cost > remaining:
                    return RouteDecision(None, f"{p.name} needs ${cost:.2f}, remaining budget ${remaining:.2f}",
                                         estimated_cost=cost, requires_approval=True)
                if not project["paid_approved"]:
                    return RouteDecision(None, f"{p.name} would cost ~${cost:.2f}; approve with "
                                               f"`approve --project {project_id}`", estimated_cost=cost,
                                         requires_approval=True)
                return RouteDecision(p, f"{p.name} (paid, approved, ~${cost:.2f})", estimated_cost=cost)
        return RouteDecision(None, "no generation provider available: " + "; ".join(reasons))

    async def run_job(self, project_id: str, scene_number: int, provider: VideoProvider,
                      req: GenerationRequest, out_dir: Path) -> GenerationResult:
        """Execute with bounded retries; every attempt is recorded in the project database."""
        rhash = content_hash(req.cache_key(provider.name))
        job_id = self.store.create_job(project_id, scene_number, provider.name, rhash, req.model_dump())
        max_retries = self.settings.execution.max_retries
        last_err = ""
        for attempt in range(1, max_retries + 2):
            self.store.update_job(job_id, status="running", attempts=attempt)
            try:
                res = await provider.generate(req, out_dir)
            except ProviderError as e:
                last_err = str(e)
                log.warning("scene %d attempt %d failed: %s", scene_number, attempt, e)
                if not e.retryable or attempt > max_retries:
                    break
                await asyncio.sleep(min(30, 2 ** attempt))
                continue
            except Exception as e:  # network errors etc.
                last_err = f"{e.__class__.__name__}: {e}"
                if attempt > max_retries:
                    break
                await asyncio.sleep(min(30, 2 ** attempt))
                continue
            self.store.update_job(job_id, status=res.status if res.status != "blocked" else "failed",
                                  output_path=res.output_path, cost_usd=res.cost_usd, error=res.error,
                                  response_json=json.dumps(res.metadata))
            if res.cost_usd:
                p = self.store.require_project(project_id)
                self.store.update_project(project_id, actual_cost_usd=p["actual_cost_usd"] + res.cost_usd)
            return res
        self.store.update_job(job_id, status="failed", error=last_err)
        return GenerationResult(provider.name, "failed", error=last_err)
