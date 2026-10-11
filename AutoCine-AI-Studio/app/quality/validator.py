"""Technical quality control of a rendered file against the production expectations.

This is *technical* validation (integrity, specs, black/frozen/silent content).
Passing it does not mean the video is creatively good - see `creative_notes`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.utils import ffmpeg as ff
from app.video.analyzer import detect_black, detect_freeze, detect_silence


@dataclass
class Expectations:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = "h264"
    audio_codec: str | None = "aac"
    require_audio: bool = True
    duration_tolerance: float = 0.15
    # intentional black at head/tail (fade in/out) that must not be flagged
    allowed_black_head: float = 0.0
    allowed_black_tail: float = 0.0
    max_black_seconds: float = 0.5
    max_freeze_seconds: float = 2.0
    max_silence_seconds: float | None = None   # None = silence allowed (e.g. no audio requested)


@dataclass
class Check:
    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    severity: str = "error"     # error | warning
    detail: str = ""


@dataclass
class QCReport:
    path: str
    passed: bool
    checks: list[Check] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    creative_notes: str = ("Technical validation only. Visual/creative quality has not been judged "
                           "and requires human review or a configured vision model.")

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["errors"] = len(self.errors)
        d["warnings"] = len(self.warnings)
        return d

    def summary(self) -> str:
        lines = [f"QC {'PASSED' if self.passed else 'FAILED'}: {self.path}"]
        for c in self.checks:
            mark = "OK " if c.passed else ("ERR" if c.severity == "error" else "WRN")
            lines.append(f"  [{mark}] {c.name}: expected={c.expected} actual={c.actual} {c.detail}".rstrip())
        return "\n".join(lines)


def validate_file(path: Path, exp: Expectations, content_checks: bool = True) -> QCReport:
    rep = QCReport(path=str(path), passed=False)
    add = rep.checks.append
    if not path.exists():
        add(Check("file_exists", False, True, False))
        return rep
    add(Check("file_exists", True, True, True))
    size = path.stat().st_size
    add(Check("file_not_empty", size > 1024, ">1KB", size))
    try:
        info = ff.probe(path)
    except ff.FFmpegError as e:
        add(Check("container_readable", False, True, False, detail=str(e)[:300]))
        return rep
    rep.metadata = info.to_dict()
    add(Check("container_readable", True, True, True))
    add(Check("has_video_stream", info.has_video, True, info.has_video))
    # Full decode catches truncated / corrupt streams that ffprobe headers miss.
    try:
        err = ff.run_ffmpeg(["-v", "error", "-i", str(path), "-f", "null", "-"], timeout=600)
        decode_errors = [ln for ln in err.splitlines() if ln.strip()]
        add(Check("decodes_without_errors", not decode_errors, 0, len(decode_errors),
                  detail="; ".join(decode_errors[:3])))
    except ff.FFmpegError as e:
        add(Check("decodes_without_errors", False, 0, "decode failed", detail=str(e)[:300]))
    if exp.duration is not None:
        tol = max(exp.duration_tolerance, 2.0 / (exp.fps or info.fps or 30))
        add(Check("duration", abs(info.duration - exp.duration) <= tol, round(exp.duration, 3),
                  round(info.duration, 3), detail=f"tolerance ±{tol:.2f}s"))
    if exp.width and exp.height:
        add(Check("resolution", (info.width, info.height) == (exp.width, exp.height),
                  f"{exp.width}x{exp.height}", f"{info.width}x{info.height}"))
        add(Check("aspect_ratio", abs(info.aspect_ratio - exp.width / exp.height) < 0.01,
                  round(exp.width / exp.height, 4), round(info.aspect_ratio, 4)))
    if exp.fps:
        add(Check("frame_rate", abs(info.fps - exp.fps) < 0.05, exp.fps, round(info.fps, 3)))
    if exp.video_codec:
        add(Check("video_codec", info.video_codec == exp.video_codec, exp.video_codec, info.video_codec))
    add(Check("pixel_format_compatible", info.pix_fmt == "yuv420p", "yuv420p", info.pix_fmt, severity="warning",
              detail="non-yuv420p may not play on phones/browsers"))
    if exp.require_audio:
        add(Check("has_audio_stream", info.has_audio, True, info.has_audio))
        if exp.audio_codec and info.has_audio:
            add(Check("audio_codec", info.audio_codec == exp.audio_codec, exp.audio_codec, info.audio_codec))
    if content_checks and info.has_video:
        black = detect_black(path, info.duration, min_d=0.1)
        unexpected = [(s, e) for s, e in black
                      if not (s <= 0.05 and e <= exp.allowed_black_head + 0.15)
                      and not (e >= info.duration - 0.05 and s >= info.duration - exp.allowed_black_tail - 0.15)]
        total = sum(e - s for s, e in unexpected)
        add(Check("unexpected_black_frames", total <= exp.max_black_seconds, f"<= {exp.max_black_seconds}s",
                  f"{total:.2f}s", detail=str(unexpected[:5]) if unexpected else ""))
        frozen = detect_freeze(path, info.duration, min_d=exp.max_freeze_seconds)
        add(Check("frozen_frames", not frozen, f"no freeze >= {exp.max_freeze_seconds}s", frozen[:5],
                  severity="warning", detail="may be intentional (stills, title cards)"))
    if content_checks and info.has_audio and exp.max_silence_seconds is not None:
        silent = detect_silence(path, info.duration, min_d=exp.max_silence_seconds)
        add(Check("silent_audio_segments", not silent, f"no silence >= {exp.max_silence_seconds}s", silent[:5],
                  severity="warning"))
    rep.passed = not rep.errors
    return rep
