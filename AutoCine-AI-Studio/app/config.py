"""Configuration loading: settings.yaml + providers.yaml + environment overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"


class PathsConfig(BaseModel):
    projects_dir: Path = Path("projects")
    assets_dir: Path = Path("assets")
    cache_dir: Path = Path("cache")
    logs_dir: Path = Path("logs")
    models_dir: Path = Path("models")
    database: Path = Path("projects/autocine.db")


class ExecutionConfig(BaseModel):
    mode: Literal["local", "cloud", "hybrid"] = "hybrid"
    max_workers: int = Field(2, ge=1, le=64)
    generation_concurrency: int = Field(1, ge=1, le=16)
    max_retries: int = Field(2, ge=0, le=10)
    min_free_disk_gb: float = 5.0


class BudgetConfig(BaseModel):
    max_project_budget_usd: float = Field(0.0, ge=0)
    allow_paid_generation: bool = False


class RenderConfig(BaseModel):
    video_codec: str = "auto"
    crf: int = Field(20, ge=0, le=51)
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    pixel_format: str = "yuv420p"
    default_fps: int = 30
    loudness_target_lufs: float = -14.0


class AnalysisWeights(BaseModel):
    sharpness: float = 0.35
    brightness: float = 0.20
    motion: float = 0.25
    stability: float = 0.20


class AnalysisConfig(BaseModel):
    scene_threshold: float = 27.0
    sample_frames: int = Field(12, ge=2, le=200)
    weights: AnalysisWeights = AnalysisWeights()


class PlanningConfig(BaseModel):
    backend: Literal["rule_based", "file"] = "rule_based"


class UIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7860


class Settings(BaseModel):
    paths: PathsConfig = PathsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    budget: BudgetConfig = BudgetConfig()
    render: RenderConfig = RenderConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    planning: PlanningConfig = PlanningConfig()
    ui: UIConfig = UIConfig()
    providers: dict[str, dict[str, Any]] = {}
    root_dir: Path = ROOT_DIR

    def resolve(self, p: Path | str) -> Path:
        """Resolve a configured path relative to the application root."""
        p = Path(p)
        return p if p.is_absolute() else (self.root_dir / p).resolve()

    @property
    def projects_dir(self) -> Path:
        return self.resolve(self.paths.projects_dir)

    @property
    def assets_dir(self) -> Path:
        return self.resolve(self.paths.assets_dir)

    @property
    def cache_dir(self) -> Path:
        return self.resolve(self.paths.cache_dir)

    @property
    def logs_dir(self) -> Path:
        return self.resolve(self.paths.logs_dir)

    @property
    def database_path(self) -> Path:
        return self.resolve(self.paths.database)

    def ensure_dirs(self) -> None:
        for d in (self.projects_dir, self.assets_dir, self.cache_dir, self.logs_dir,
                  self.resolve(self.paths.models_dir)):
            d.mkdir(parents=True, exist_ok=True)


def _parse_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """AUTOCINE_<SECTION>_<KEY>=value overrides plus the documented budget vars."""
    for key, value in os.environ.items():
        if not key.startswith("AUTOCINE_"):
            continue
        parts = key[len("AUTOCINE_"):].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, field = parts
        if section in data and isinstance(data[section], dict):
            data[section][field] = value
    budget = data.setdefault("budget", {})
    if os.environ.get("MAX_PROJECT_BUDGET_USD", "").strip():
        budget["max_project_budget_usd"] = float(os.environ["MAX_PROJECT_BUDGET_USD"])
    if os.environ.get("ALLOW_PAID_GENERATION", "").strip():
        budget["allow_paid_generation"] = _parse_bool(os.environ["ALLOW_PAID_GENERATION"])
    if os.environ.get("AUTOCINE_COMFYUI_URL", "").strip():
        data.setdefault("providers", {}).setdefault("comfyui", {})["base_url"] = os.environ["AUTOCINE_COMFYUI_URL"]


def load_settings(settings_file: Path | None = None, providers_file: Path | None = None,
                  root_dir: Path | None = None) -> Settings:
    load_dotenv(ROOT_DIR / ".env", override=False)
    settings_file = settings_file or CONFIG_DIR / "settings.yaml"
    providers_file = providers_file or CONFIG_DIR / "providers.yaml"
    data: dict[str, Any] = {}
    if settings_file.exists():
        data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
    if providers_file.exists():
        data["providers"] = yaml.safe_load(providers_file.read_text(encoding="utf-8")) or {}
    _apply_env_overrides(data)
    if root_dir is not None:
        data["root_dir"] = root_dir
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
