"""Pydantic schemas for production plans and storyboards (the system's contract)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.video.transitions import TransitionType

Style = Literal["cinematic", "product_commercial", "technology_showcase", "fast_paced", "documentary",
                "relaxed_storytelling"]
AspectRatio = Literal["9:16", "16:9", "1:1", "4:5"]


class AudioPlan(BaseModel):
    music: bool = True
    music_style: str = "cinematic ambient"
    narration: bool = False
    sound_effects: bool = False
    subtitles: bool = False
    voice: str | None = None
    speech_rate: float = Field(1.0, ge=0.5, le=2.0)


class ExportPlan(BaseModel):
    format: Literal["mp4"] = "mp4"
    codec: Literal["h264"] = "h264"
    crf: int = 20


class StyleSpec(BaseModel):
    """Project-level visual bible, injected into every scene prompt for consistency."""
    visual_style: str
    subject_identity: str = Field(..., description="Exact recurring subject description, reused verbatim")
    materials: str = ""
    color_palette: str = ""
    lighting: str = ""
    environment: str = ""
    camera_language: str = ""
    negative_prompt: str = ("blurry, low quality, distorted, deformed, extra limbs, watermark, text, logo, "
                            "jpeg artifacts, flicker")
    reference_images: list[str] = []
    seed: int = 42


class ProductionPlan(BaseModel):
    project_name: str
    mode: Literal["text_to_video", "video_to_video"]
    topic: str
    target_audience: str = "general online audience"
    duration_seconds: float = Field(..., gt=1, le=600)
    aspect_ratio: AspectRatio = "9:16"
    resolution: str = "1080x1920"
    fps: int = Field(30, ge=12, le=60)
    style: Style = "cinematic"
    scene_count: int = Field(..., ge=1, le=60)
    narrative_structure: list[str] = []
    transition_preference: list[TransitionType] = ["crossfade"]
    transition_duration: float = Field(0.5, ge=0, le=2)
    color_grade: str = "cinematic_teal_orange"
    audio: AudioPlan = AudioPlan()
    export: ExportPlan = ExportPlan()
    style_spec: StyleSpec
    planner: str = "rule_based"
    notes: list[str] = []

    @model_validator(mode="after")
    def _resolution_matches_aspect(self) -> "ProductionPlan":
        w, h = (int(x) for x in self.resolution.lower().split("x"))
        a, b = (int(x) for x in self.aspect_ratio.split(":"))
        if abs(w / h - a / b) > 0.02:
            raise ValueError(f"resolution {self.resolution} does not match aspect {self.aspect_ratio}")
        return self

    @property
    def width(self) -> int:
        return int(self.resolution.lower().split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.lower().split("x")[1])


class Scene(BaseModel):
    scene_number: int = Field(..., ge=1)
    beat: str = ""
    description: str
    visual_prompt: str = ""
    negative_prompt: str = ""
    camera_angle: str = "eye level"
    camera_movement: str = "static"
    lighting: str = ""
    environment: str = ""
    subject: str = ""
    duration: float = Field(..., gt=0.3)
    generation_duration: float = Field(5.0, gt=0, description="Length requested from the generator")
    transition_in: TransitionType = "cut"
    transition_duration: float = Field(0.0, ge=0)
    sound_effects: list[str] = []
    narration: str | None = None
    reference_image: str | None = None


class Storyboard(BaseModel):
    project_name: str
    total_duration: float
    scenes: list[Scene] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _duration_consistent(self) -> "Storyboard":
        rendered = sum(s.duration for s in self.scenes) - sum(s.transition_duration for s in self.scenes[1:])
        if abs(rendered - self.total_duration) > 0.05:
            raise ValueError(f"Scene durations render to {rendered:.2f}s, expected {self.total_duration:.2f}s "
                             "(remember transitions overlap adjacent scenes)")
        for s in self.scenes:
            if s.transition_duration > s.duration / 2:
                raise ValueError(f"Scene {s.scene_number}: transition longer than half the scene")
        return self

    def to_text(self) -> str:
        out = [f"STORYBOARD: {self.project_name} ({self.total_duration:.1f}s)"]
        for s in self.scenes:
            out += [f"\nSCENE {s.scene_number} - {s.beat}", f"Duration: {s.duration:.2f} s",
                    f"Visual: {s.description}", f"Camera: {s.camera_angle}, {s.camera_movement}",
                    f"Lighting: {s.lighting}", f"Sound: {', '.join(s.sound_effects) or '-'}",
                    f"Transition in: {s.transition_in} ({s.transition_duration:.2f}s)"]
            if s.narration:
                out.append(f"Narration: \"{s.narration}\"")
        return "\n".join(out)
