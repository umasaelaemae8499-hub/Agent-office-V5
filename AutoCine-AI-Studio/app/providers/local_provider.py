"""ComfyUI adapter (local generation) using ComfyUI's own HTTP API:

  GET  /system_stats            - health + GPU/VRAM info
  POST /prompt                  - queue an API-format workflow -> {"prompt_id": ...}
  GET  /history/{prompt_id}     - job status + outputs once finished
  GET  /view?filename=&subfolder=&type=  - download an output file
  POST /upload/image            - upload a reference image (multipart field "image")

The workflow itself is user-supplied (exported via "Save (API Format)") so any
model the user's hardware can run - Wan, LTX-Video, HunyuanVideo, SDXL - works
without this code hard-coding a model graph. Placeholders are substituted:
{{PROMPT}} {{NEGATIVE_PROMPT}} {{WIDTH}} {{HEIGHT}} {{FRAMES}} {{FPS}} {{SEED}}
{{DURATION}} {{REFERENCE_IMAGE}}.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.providers.base import (Capabilities, GenerationRequest, GenerationResult, ProviderError,
                                ProviderStatus, VideoProvider)
from app.utils.paths import sanitize_filename

log = logging.getLogger(__name__)
VIDEO_EXT = {".mp4", ".webm", ".mov", ".gif", ".webp", ".mkv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def substitute(workflow: Any, values: dict[str, Any]) -> Any:
    """Replace {{KEY}} placeholders. A string that is exactly one placeholder takes the typed
    value (ints stay ints, as ComfyUI requires); embedded placeholders are string-formatted."""
    if isinstance(workflow, dict):
        return {k: substitute(v, values) for k, v in workflow.items()}
    if isinstance(workflow, list):
        return [substitute(v, values) for v in workflow]
    if isinstance(workflow, str):
        for key, val in values.items():
            token = "{{" + key + "}}"
            if workflow == token:
                return val
            if token in workflow:
                workflow = workflow.replace(token, str(val))
        return workflow
    return workflow


def find_outputs(history_entry: dict[str, Any]) -> list[dict[str, Any]]:
    files = []
    for node_out in (history_entry.get("outputs") or {}).values():
        for value in node_out.values():
            if isinstance(value, list):
                files += [f for f in value if isinstance(f, dict) and "filename" in f]
    return files


class ComfyUIProvider(VideoProvider):
    name = "comfyui"
    is_local = True
    is_paid = False
    verified = True

    def __init__(self, base_url: str, video_workflow: Path | None, image_workflow: Path | None,
                 timeout: float = 1800, poll_interval: float = 3.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.video_workflow = video_workflow
        self.image_workflow = image_workflow
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._transport = transport
        self.client_id = uuid.uuid4().hex

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(30.0),
                                 transport=self._transport)

    async def check_available(self) -> ProviderStatus:
        try:
            async with self._client() as c:
                r = await c.get("/system_stats")
                r.raise_for_status()
        except Exception as e:
            return ProviderStatus(False, f"ComfyUI not reachable at {self.base_url}: {e.__class__.__name__}")
        if not (self.video_workflow and self.video_workflow.exists()) and \
                not (self.image_workflow and self.image_workflow.exists()):
            return ProviderStatus(False, "ComfyUI is running but no API-format workflow file is configured "
                                         "(config/providers.yaml -> comfyui.video_workflow / image_workflow)")
        return ProviderStatus(True, "ok")

    def capabilities(self) -> Capabilities:
        has_v = bool(self.video_workflow and self.video_workflow.exists())
        has_i = bool(self.image_workflow and self.image_workflow.exists())
        return Capabilities(text_to_video=has_v, text_to_image=has_i, image_to_video=has_v,
                            reference_images=has_v, max_duration=10.0 if has_v else 0.0)

    async def _upload_reference(self, c: httpx.AsyncClient, path: str) -> str:
        p = Path(path)
        with open(p, "rb") as f:
            r = await c.post("/upload/image", files={"image": (sanitize_filename(p.name), f.read())},
                             data={"overwrite": "true"})
        r.raise_for_status()
        data = r.json()
        return f"{data['subfolder']}/{data['name']}" if data.get("subfolder") else data["name"]

    async def generate(self, req: GenerationRequest, out_dir: Path) -> GenerationResult:
        wf_path = self.video_workflow if req.kind == "video" else self.image_workflow
        if not wf_path or not wf_path.exists():
            return GenerationResult(self.name, "blocked", kind=req.kind,
                                    error=f"no ComfyUI {req.kind} workflow configured")
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
        workflow = {k: v for k, v in workflow.items() if not k.startswith("_")}
        out_dir.mkdir(parents=True, exist_ok=True)
        async with self._client() as c:
            ref_name = await self._upload_reference(c, req.reference_image) if req.reference_image else ""
            values = {"PROMPT": req.prompt, "NEGATIVE_PROMPT": req.negative_prompt, "WIDTH": req.width,
                      "HEIGHT": req.height, "FPS": req.fps, "SEED": req.seed, "DURATION": req.duration,
                      "FRAMES": int(round(req.duration * req.fps)) + 1, "REFERENCE_IMAGE": ref_name}
            graph = substitute(copy.deepcopy(workflow), values)
            r = await c.post("/prompt", json={"prompt": graph, "client_id": self.client_id})
            if r.status_code != 200:
                raise ProviderError(f"ComfyUI rejected workflow ({r.status_code}): {r.text[:500]}")
            prompt_id = r.json().get("prompt_id")
            if not prompt_id:
                raise ProviderError(f"ComfyUI returned no prompt_id: {r.text[:300]}")
            log.info("ComfyUI job %s queued", prompt_id)
            deadline = time.monotonic() + self.timeout
            entry: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                h = await c.get(f"/history/{prompt_id}")
                h.raise_for_status()
                entry = h.json().get(prompt_id)
                if entry:
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        msgs = json.dumps(status.get("messages", []))[:800]
                        raise ProviderError(f"ComfyUI execution error: {msgs}", retryable=False)
                    if status.get("completed", True) and entry.get("outputs"):
                        break
                await asyncio.sleep(self.poll_interval)
            else:
                raise ProviderError(f"ComfyUI job {prompt_id} timed out after {self.timeout}s", retryable=True)
            files = find_outputs(entry or {})
            wanted = VIDEO_EXT if req.kind == "video" else IMAGE_EXT
            pick = next((f for f in files if Path(f["filename"]).suffix.lower() in wanted), None)
            if not pick:
                raise ProviderError(f"ComfyUI job finished without a {req.kind} output: {files}")
            dl = await c.get("/view", params={"filename": pick["filename"], "subfolder": pick.get("subfolder", ""),
                                              "type": pick.get("type", "output")}, timeout=300)
            dl.raise_for_status()
            dest = out_dir / sanitize_filename(f"{prompt_id[:8]}_{pick['filename']}")
            dest.write_bytes(dl.content)
        return GenerationResult(self.name, "succeeded", str(dest), kind=req.kind, cost_usd=0.0,
                                metadata={"prompt_id": prompt_id, "workflow": str(wf_path)})
