"""Clip analysis: ffprobe metadata, PySceneDetect boundaries, OpenCV heuristics,
FFmpeg black/freeze/silence detection and representative-frame extraction.

The scores here are *technical heuristics* (focus, exposure, motion, stability).
They do not measure artistic quality, and no semantic understanding is claimed
unless a vision model is configured (see `describe_frames`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.config import AnalysisWeights
from app.utils import ffmpeg as ff

log = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float
    end: float
    sharpness: float = 0.0       # variance of Laplacian (higher = crisper)
    brightness: float = 0.0      # mean luma 0..1
    motion: float = 0.0          # mean abs frame difference 0..1
    score: float = 0.0
    keyframe: str | None = None
    description: str | None = None  # only set by a real vision model

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ClipAnalysis:
    path: str
    info: dict[str, Any]
    segments: list[Segment] = field(default_factory=list)
    black_intervals: list[tuple[float, float]] = field(default_factory=list)
    freeze_intervals: list[tuple[float, float]] = field(default_factory=list)
    silence_intervals: list[tuple[float, float]] = field(default_factory=list)
    mean_brightness: float = 0.0
    score: float = 0.0
    semantic_analysis: str = "not performed (no vision model configured)"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClipAnalysis":
        d = dict(d)
        d["segments"] = [Segment(**s) for s in d.get("segments", [])]
        for k in ("black_intervals", "freeze_intervals", "silence_intervals"):
            d[k] = [tuple(x) for x in d.get(k, [])]
        return cls(**d)


# ---------------------------------------------------------------- FFmpeg detectors
def _parse_intervals(stderr: str, start_key: str, end_key: str, duration: float) -> list[tuple[float, float]]:
    starts = [float(m) for m in re.findall(rf"{start_key}[:=]\s*([\d.]+)", stderr)]
    ends = [float(m) for m in re.findall(rf"{end_key}[:=]\s*([\d.]+)", stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        out.append((round(s, 3), round(e, 3)))
    return out


def detect_black(path: Path, duration: float, min_d: float = 0.3) -> list[tuple[float, float]]:
    err = ff.run_ffmpeg(["-i", str(path), "-vf", f"blackdetect=d={min_d}:pix_th=0.10", "-an", "-f", "null", "-"])
    return _parse_intervals(err, "black_start", "black_end", duration)


def detect_freeze(path: Path, duration: float, min_d: float = 1.0) -> list[tuple[float, float]]:
    err = ff.run_ffmpeg(["-i", str(path), "-vf", f"freezedetect=n=0.003:d={min_d}", "-map", "0:v:0",
                         "-f", "null", "-"])
    return _parse_intervals(err, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end", duration)


def detect_silence(path: Path, duration: float, min_d: float = 1.0, noise_db: int = -50) -> list[tuple[float, float]]:
    err = ff.run_ffmpeg(["-i", str(path), "-af", f"silencedetect=n={noise_db}dB:d={min_d}", "-vn", "-f", "null", "-"])
    return _parse_intervals(err, "silence_start", "silence_end", duration)


# ---------------------------------------------------------------- scene detection
def detect_scenes(path: Path, threshold: float = 27.0, min_scene_len_s: float = 0.6) -> list[tuple[float, float]]:
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        log.warning("PySceneDetect not installed; treating each file as one scene")
        return []
    info = ff.probe(path)
    min_frames = max(1, int(min_scene_len_s * (info.fps or 30)))
    scenes = detect(str(path), ContentDetector(threshold=threshold, min_scene_len=min_frames))

    def secs(tc) -> float:  # `seconds` property in PySceneDetect >=0.7, get_seconds() before
        v = getattr(type(tc), "seconds", None)
        return float(tc.seconds) if isinstance(v, property) else float(tc.get_seconds())

    return [(secs(s), secs(e)) for s, e in scenes]


# ---------------------------------------------------------------- OpenCV heuristics
def _frame_metrics(path: Path, start: float, end: float, samples: int) -> tuple[float, float, float, np.ndarray | None]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open {path}")
    sharp, bright, motion = [], [], []
    prev = None
    best_frame, best_sharp = None, -1.0
    span = max(end - start, 0.01)
    times = [start + span * (i + 0.5) / samples for i in range(samples)]
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (320, int(320 * gray.shape[0] / max(gray.shape[1], 1))))
            s = float(cv2.Laplacian(small, cv2.CV_64F).var())
            sharp.append(s)
            bright.append(float(small.mean()) / 255.0)
            if prev is not None and prev.shape == small.shape:
                motion.append(float(np.abs(small.astype(np.int16) - prev.astype(np.int16)).mean()) / 255.0)
            prev = small
            if s > best_sharp:
                best_sharp, best_frame = s, frame
    finally:
        cap.release()
    if not sharp:
        return 0.0, 0.0, 0.0, None
    return float(np.median(sharp)), float(np.mean(bright)), float(np.mean(motion)) if motion else 0.0, best_frame


def score_segment(seg: Segment, w: AnalysisWeights, black_frac: float = 0.0, freeze_frac: float = 0.0) -> float:
    """Weighted 0..1 heuristic. Mid exposure, crisp focus, moderate motion score best."""
    sharp_n = min(1.0, np.log1p(seg.sharpness) / np.log1p(1500))
    bright_n = max(0.0, 1.0 - abs(seg.brightness - 0.45) / 0.45)
    # moderate motion is "cinematic"; none (static) or extreme (shaky) score lower
    motion_n = float(np.exp(-((seg.motion - 0.06) ** 2) / (2 * 0.05 ** 2)))
    stability_n = max(0.0, 1.0 - seg.motion / 0.3)
    total = w.sharpness + w.brightness + w.motion + w.stability
    s = (w.sharpness * sharp_n + w.brightness * bright_n + w.motion * motion_n + w.stability * stability_n) / total
    s *= (1.0 - 0.9 * black_frac) * (1.0 - 0.6 * freeze_frac)
    return round(float(s), 4)


def _overlap(a: tuple[float, float], intervals: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(a[1], e) - max(a[0], s)) for s, e in intervals)


def analyze_clip(path: Path, frames_dir: Path | None = None, weights: AnalysisWeights | None = None,
                 scene_threshold: float = 27.0, samples: int = 12) -> ClipAnalysis:
    weights = weights or AnalysisWeights()
    info = ff.probe(path)
    ca = ClipAnalysis(path=str(path), info=info.to_dict())
    if not info.has_video:
        ca.warnings.append("no video stream")
        return ca
    if info.duration <= 0:
        ca.warnings.append("zero duration")
        return ca
    ca.black_intervals = detect_black(path, info.duration)
    ca.freeze_intervals = detect_freeze(path, info.duration)
    if info.has_audio:
        ca.silence_intervals = detect_silence(path, info.duration)
    else:
        ca.warnings.append("no audio stream")
    bounds = detect_scenes(path, scene_threshold) or [(0.0, info.duration)]
    per_scene = max(3, samples // max(1, len(bounds)))
    for k, (s, e) in enumerate(bounds):
        seg = Segment(start=round(s, 3), end=round(e, 3))
        seg.sharpness, seg.brightness, seg.motion, frame = _frame_metrics(path, s, e, per_scene)
        dur = max(seg.duration, 1e-6)
        seg.score = score_segment(seg, weights, _overlap((s, e), ca.black_intervals) / dur,
                                  _overlap((s, e), ca.freeze_intervals) / dur)
        if frames_dir is not None and frame is not None:
            import cv2
            frames_dir.mkdir(parents=True, exist_ok=True)
            kf = frames_dir / f"{Path(path).stem}_scene{k:03d}.jpg"
            cv2.imwrite(str(kf), frame)
            seg.keyframe = str(kf)
        ca.segments.append(seg)
    total = sum(sg.duration for sg in ca.segments) or 1.0
    ca.mean_brightness = round(sum(sg.brightness * sg.duration for sg in ca.segments) / total, 4)
    ca.score = round(sum(sg.score * sg.duration for sg in ca.segments) / total, 4)
    if _overlap((0, info.duration), ca.black_intervals) > 0.5 * info.duration:
        ca.warnings.append("mostly black")
    if _overlap((0, info.duration), ca.freeze_intervals) > 0.95 * info.duration:
        # A completely static shot (product still, title card) is not a glitch: keep it,
        # score it like a still and let the editor animate it.
        ca.warnings.append("static shot (no motion) - treated as a still")
        ca.freeze_intervals = []
        for seg in ca.segments:
            seg.score = score_segment(seg, weights, _overlap((seg.start, seg.end), ca.black_intervals) / max(seg.duration, 1e-6), 0.0)
        ca.score = round(sum(sg.score * sg.duration for sg in ca.segments) / total, 4)
    elif _overlap((0, info.duration), ca.freeze_intervals) > 0.5 * info.duration:
        ca.warnings.append("mostly frozen")
    return ca


# ---------------------------------------------------------------- optional vision model
class VisionDescriber(Protocol):
    name: str

    def describe(self, image_path: Path) -> str: ...


def describe_frames(analysis: ClipAnalysis, describer: VisionDescriber | None) -> ClipAnalysis:
    """Attach semantic descriptions ONLY when a real vision model is supplied."""
    if describer is None:
        return analysis
    for seg in analysis.segments:
        if seg.keyframe:
            seg.description = describer.describe(Path(seg.keyframe))
    analysis.semantic_analysis = f"performed by {describer.name}"
    return analysis
