"""JSON-serialisable editing timeline + duration / transition-overlap maths."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.video.transitions import TransitionType

FitMode = Literal["auto", "cover", "contain", "blur_pad"]


class Effects(BaseModel):
    zoom: float = Field(0.0, ge=-0.5, le=0.5, description="Ken-Burns zoom over the clip; +0.1 = 10% push-in")
    pan: Literal["none", "left", "right", "up", "down"] = "none"
    blur: float = Field(0.0, ge=0, le=20)
    sharpen: float = Field(0.0, ge=0, le=2)
    vignette: bool = False
    auto_level: float | None = Field(None, description="Measured mean luma 0..1 for exposure correction")


class Transition(BaseModel):
    type: TransitionType = "cut"
    duration: float = Field(0.0, ge=0, le=5)

    @model_validator(mode="after")
    def _cut_has_no_duration(self) -> "Transition":
        if self.type == "cut":
            self.duration = 0.0
        elif self.duration <= 0:
            self.duration = 0.5
        return self


class VideoClip(BaseModel):
    source: str
    kind: Literal["video", "image"] = "video"
    in_point: float = Field(0.0, ge=0)
    out_point: float = Field(..., gt=0, description="Seconds in the source (image: display length)")
    speed: float = Field(1.0, gt=0.1, le=8.0)
    fit: FitMode = "auto"
    effects: Effects = Effects()
    color: str = "none"
    use_audio: bool = True
    transition_in: Transition = Transition()  # transition FROM previous clip INTO this one
    label: str | None = None
    scene_number: int | None = None

    @model_validator(mode="after")
    def _check_range(self) -> "VideoClip":
        if self.out_point <= self.in_point:
            raise ValueError(f"out_point ({self.out_point}) must be after in_point ({self.in_point})")
        return self

    @property
    def duration(self) -> float:
        """Duration on the timeline (after speed change)."""
        return (self.out_point - self.in_point) / self.speed


class AudioCue(BaseModel):
    source: str
    start: float = Field(0.0, ge=0, description="Timeline position in seconds")
    volume: float = Field(1.0, ge=0, le=4)
    label: str | None = None
    license: str | None = None


class MusicTrack(BaseModel):
    source: str
    volume: float = Field(0.35, ge=0, le=4)
    offset: float = Field(0.0, ge=0, description="Start reading the music file at this second")
    loop: bool = True
    duck_under_narration: bool = True
    fade_in: float = Field(1.0, ge=0)
    fade_out: float = Field(2.0, ge=0)
    license: str | None = None


class SubtitleTrack(BaseModel):
    path: str
    burn_in: bool = True


class OutputSpec(BaseModel):
    width: int = Field(1080, ge=16, le=7680)
    height: int = Field(1920, ge=16, le=7680)
    fps: float = Field(30, gt=0, le=120)
    video_codec: str = "auto"
    crf: int = 20
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    sample_rate: int = 48000
    loudness_lufs: float | None = -14.0
    fade_in_from_black: float = Field(0.0, ge=0)
    fade_out_to_black: float = Field(0.0, ge=0)

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        return v - (v % 2)  # yuv420p requires even dimensions


class Timeline(BaseModel):
    clips: list[VideoClip] = Field(..., min_length=1)
    music: MusicTrack | None = None
    narration: list[AudioCue] = []
    sfx: list[AudioCue] = []
    subtitles: SubtitleTrack | None = None
    output: OutputSpec = OutputSpec()
    clip_audio_volume: float = Field(1.0, ge=0, le=4)

    @model_validator(mode="after")
    def _check_transitions(self) -> "Timeline":
        if self.clips[0].transition_in.type != "cut":
            self.clips[0].transition_in = Transition(type="cut")
        for prev, cur in zip(self.clips, self.clips[1:]):
            d = cur.transition_in.duration
            if d and d >= min(prev.duration, cur.duration):
                raise ValueError(f"Transition of {d:.2f}s into clip {cur.label or cur.source} is longer than "
                                 f"an adjacent clip ({prev.duration:.2f}s / {cur.duration:.2f}s)")
        return self

    def clip_start_times(self) -> list[float]:
        """Timeline start time of each clip (transitions overlap the previous clip's tail)."""
        starts, t = [], 0.0
        for i, clip in enumerate(self.clips):
            if i:
                t -= clip.transition_in.duration
            starts.append(t)
            t += clip.duration
        return starts

    @property
    def total_duration(self) -> float:
        return sum(c.duration for c in self.clips) - sum(c.transition_in.duration for c in self.clips[1:])

    def source_paths(self) -> list[Path]:
        paths = [Path(c.source) for c in self.clips]
        if self.music:
            paths.append(Path(self.music.source))
        paths += [Path(a.source) for a in [*self.narration, *self.sfx]]
        return paths


def fit_durations_to_target(durations: list[float], transitions: list[float], target: float,
                            min_clip: float = 0.6) -> list[float]:
    """Scale clip durations so sum(durations) - sum(transitions) == target.

    `transitions[i]` is the overlap INTO clip i (transitions[0] is ignored).
    """
    overlap = sum(transitions[1:])
    needed = target + overlap
    current = sum(durations)
    if current <= 0:
        raise ValueError("durations must be positive")
    scaled = [max(min_clip, d * needed / current) for d in durations]
    # Correct residual introduced by the min_clip clamp on the largest clip.
    residual = needed - sum(scaled)
    scaled[scaled.index(max(scaled))] += residual
    return [round(d, 3) for d in scaled]


def fit_durations_with_caps(desired: list[float], transitions: list[float], target: float,
                            caps: list[float], min_clip: float = 0.6) -> list[float]:
    """Like fit_durations_to_target but no clip may exceed caps[i] (available footage).

    Water-filling: scale uncapped clips, clamp, redistribute the remainder. If total
    capacity is insufficient the result is shorter than target (caller must handle).
    """
    needed = target + sum(transitions[1:])
    durs = [min(d, c) for d, c in zip(desired, caps)]
    for _ in range(20):
        free = [i for i in range(len(durs)) if durs[i] < caps[i] - 1e-6]
        residual = needed - sum(durs)
        if abs(residual) < 1e-4 or (residual > 0 and not free):
            break
        pool = free if residual > 0 else list(range(len(durs)))
        base = sum(durs[i] for i in pool) or 1.0
        for i in pool:
            durs[i] = min(caps[i], max(min_clip, durs[i] + residual * durs[i] / base))
    return [round(d, 3) for d in durs]
