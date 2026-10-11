"""Provider interface shared by every generation backend (local or cloud)."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class GenerationRequest(BaseModel):
    kind: Literal["video", "image"] = "video"
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str = ""
    duration: float = Field(5.0, gt=0, le=60)
    aspect_ratio: str = "16:9"
    width: int = 1280
    height: int = 720
    fps: int = 24
    seed: int = 42
    reference_image: str | None = None

    def cache_key(self, provider: str) -> dict[str, Any]:
        return {"provider": provider, **self.model_dump()}


@dataclass
class GenerationResult:
    provider: str
    status: Literal["succeeded", "failed", "blocked"]
    output_path: str | None = None
    kind: str = "video"
    cost_usd: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Capabilities:
    text_to_video: bool = False
    image_to_video: bool = False
    text_to_image: bool = False
    reference_images: bool = False
    max_duration: float = 0.0
    aspect_ratios: tuple[str, ...] = ("16:9", "9:16", "1:1")


@dataclass
class ProviderStatus:
    available: bool
    reason: str = ""


class ProviderError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class VideoProvider(abc.ABC):
    """Common adapter contract: auth, capability discovery, generate, poll, download, cost."""

    name: str = "base"
    is_local: bool = False
    is_paid: bool = False
    verified: bool = False   # True only for adapters tested against the real, documented API

    @abc.abstractmethod
    async def check_available(self) -> ProviderStatus: ...

    @abc.abstractmethod
    def capabilities(self) -> Capabilities: ...

    def estimate_cost(self, req: GenerationRequest) -> float:
        return 0.0

    @abc.abstractmethod
    async def generate(self, req: GenerationRequest, out_dir: Path) -> GenerationResult: ...

    async def generate_video(self, prompt: str, duration: float, aspect_ratio: str,
                             reference_image: str | None = None, out_dir: Path | None = None,
                             **kw: Any) -> GenerationResult:
        req = GenerationRequest(kind="video", prompt=prompt, duration=duration, aspect_ratio=aspect_ratio,
                                reference_image=reference_image, **kw)
        return await self.generate(req, out_dir or Path("."))
