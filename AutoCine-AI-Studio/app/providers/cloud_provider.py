"""Cloud provider scaffolding.

`CloudJobProvider` implements the generic, provider-agnostic job lifecycle (auth header,
submit, poll with timeout, bounded retries, download, usage tracking). Concrete adapters
must supply the provider-specific request/response mapping *after* verifying the
provider's current official API documentation.

Kling and Runway are registered as UNVERIFIED: their official docs could not be verified
from the build environment, so no endpoints are hard-coded and they always report
unavailable. See docs/PROVIDERS.md for how to add a verified adapter.
"""

from __future__ import annotations

import abc
import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from app.providers.base import (Capabilities, GenerationRequest, GenerationResult, ProviderError,
                                ProviderStatus, VideoProvider)
from app.utils.paths import sanitize_filename


class CloudJobProvider(VideoProvider):
    is_local = False
    is_paid = True
    api_key_env: str = ""
    cost_per_second_usd: float = 0.0
    timeout: float = 900
    poll_interval: float = 5.0

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def estimate_cost(self, req: GenerationRequest) -> float:
        return round(self.cost_per_second_usd * req.duration, 4)

    async def check_available(self) -> ProviderStatus:
        if not self.verified:
            return ProviderStatus(False, f"{self.name} adapter not implemented: API not verified")
        if not self.api_key:
            return ProviderStatus(False, f"{self.api_key_env} not set")
        return ProviderStatus(True, "ok")

    # ---- provider-specific hooks ----
    @abc.abstractmethod
    def base_url(self) -> str: ...

    @abc.abstractmethod
    def auth_headers(self) -> dict[str, str]: ...

    @abc.abstractmethod
    def submit_request(self, req: GenerationRequest) -> tuple[str, dict[str, Any]]:
        """Return (path, json_body)."""

    @abc.abstractmethod
    def parse_submit(self, data: dict[str, Any]) -> str:
        """Return the job id."""

    @abc.abstractmethod
    def status_path(self, job_id: str) -> str: ...

    @abc.abstractmethod
    def parse_status(self, data: dict[str, Any]) -> tuple[str, str | None, str | None]:
        """Return (state in {'running','succeeded','failed'}, output_url, error)."""

    async def generate(self, req: GenerationRequest, out_dir: Path) -> GenerationResult:
        st = await self.check_available()
        if not st.available:
            return GenerationResult(self.name, "blocked", error=st.reason)
        async with httpx.AsyncClient(base_url=self.base_url(), headers=self.auth_headers(),
                                     timeout=httpx.Timeout(60.0), transport=self._transport) as c:
            path, body = self.submit_request(req)
            r = await c.post(path, json=body)
            if r.status_code >= 400:
                raise ProviderError(f"{self.name} submit failed {r.status_code}: {r.text[:400]}",
                                    retryable=r.status_code in (429, 500, 502, 503, 504))
            job_id = self.parse_submit(r.json())
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                s = await c.get(self.status_path(job_id))
                if s.status_code >= 400:
                    raise ProviderError(f"{self.name} status failed {s.status_code}", retryable=s.status_code >= 500)
                state, url, err = self.parse_status(s.json())
                if state == "failed":
                    raise ProviderError(f"{self.name} job {job_id} failed: {err}")
                if state == "succeeded":
                    if not url or not url.startswith("https://"):
                        raise ProviderError(f"{self.name} returned an invalid output URL")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dest = out_dir / sanitize_filename(f"{self.name}_{job_id}.mp4")
                    async with httpx.AsyncClient(timeout=300, transport=self._transport) as dl:
                        resp = await dl.get(url)
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    return GenerationResult(self.name, "succeeded", str(dest), cost_usd=self.estimate_cost(req),
                                            metadata={"job_id": job_id})
                await asyncio.sleep(self.poll_interval)
        raise ProviderError(f"{self.name} job timed out", retryable=True)


class UnverifiedCloudProvider(VideoProvider):
    """Registered so routing/reports can name it, but never generates anything."""

    is_local = False
    is_paid = True
    verified = False

    def __init__(self, name: str, api_key_env: str) -> None:
        self.name = name
        self.api_key_env = api_key_env

    async def check_available(self) -> ProviderStatus:
        has_key = bool(os.environ.get(self.api_key_env, "").strip())
        return ProviderStatus(False, f"{self.name}: adapter blocked - official API not yet verified"
                                     f"{' (key present)' if has_key else ''}")

    def capabilities(self) -> Capabilities:
        return Capabilities()

    async def generate(self, req: GenerationRequest, out_dir: Path) -> GenerationResult:
        return GenerationResult(self.name, "blocked", error="adapter not implemented (API unverified)")
