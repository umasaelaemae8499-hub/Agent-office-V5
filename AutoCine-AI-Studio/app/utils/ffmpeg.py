"""Thin, safe wrappers around ffmpeg / ffprobe (argument arrays only, never shell=True)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str | None:
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str | None:
    return os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")


def _require(binary: str | None, name: str) -> str:
    if not binary:
        raise FFmpegError(f"{name} not found. Install FFmpeg and add it to PATH "
                          f"(Windows: `winget install Gyan.FFmpeg`) or set {name.upper()}_PATH.")
    return binary


def run_ffmpeg(args: Sequence[str], timeout: float | None = None,
               progress: Callable[[float], None] | None = None,
               total_seconds: float | None = None, cwd: Path | str | None = None) -> str:
    """Run ffmpeg with *args* (without the binary). Returns stderr text."""
    cmd = [_require(ffmpeg_bin(), "ffmpeg"), "-hide_banner", "-nostdin", *map(str, args)]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    if progress is None:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd)
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg failed (exit {proc.returncode}): {_tail(proc.stderr)}", proc.stderr)
        return proc.stderr
    # Streaming mode: parse "time=HH:MM:SS.xx" for progress callbacks.
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", cwd=cwd)
    lines: list[str] = []
    assert proc.stderr is not None
    for line in iter(proc.stderr.readline, ""):
        lines.append(line)
        m = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
        if m and total_seconds:
            t = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            progress(min(1.0, t / total_seconds))
    proc.wait(timeout=timeout)
    stderr = "".join(lines)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed (exit {proc.returncode}): {_tail(stderr)}", stderr)
    if total_seconds:
        progress(1.0)
    return stderr


def _tail(text: str, n: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def ffprobe_json(path: Path | str) -> dict[str, Any]:
    cmd = [_require(ffprobe_bin(), "ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path}: {_tail(proc.stderr)}", proc.stderr)
    return json.loads(proc.stdout or "{}")


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in {"0/0", "N/A"}:
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str | None = None
    audio_codec: str | None = None
    pix_fmt: str | None = None
    sample_rate: int = 0
    channels: int = 0
    bit_rate: int = 0
    size_bytes: int = 0
    format_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("extra", None)
        return d


def probe(path: Path | str) -> MediaInfo:
    data = ffprobe_json(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or (v or a or {}).get("duration") or 0.0)
    info = MediaInfo(path=str(path), duration=duration, has_video=v is not None,
                     has_audio=a is not None, size_bytes=int(fmt.get("size") or 0),
                     bit_rate=int(fmt.get("bit_rate") or 0), format_name=fmt.get("format_name", ""))
    if v:
        info.width, info.height = int(v.get("width", 0)), int(v.get("height", 0))
        info.fps = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate"))
        info.video_codec, info.pix_fmt = v.get("codec_name"), v.get("pix_fmt")
        rotation = _rotation(v)
        if rotation in (90, 270):
            info.width, info.height = info.height, info.width
        info.extra["rotation"] = rotation
    if a:
        info.audio_codec = a.get("codec_name")
        info.sample_rate = int(a.get("sample_rate") or 0)
        info.channels = int(a.get("channels") or 0)
    return info


def _rotation(stream: dict[str, Any]) -> int:
    rot = stream.get("tags", {}).get("rotate")
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        return int(float(rot)) % 360 if rot is not None else 0
    except ValueError:
        return 0


@lru_cache(maxsize=1)
def list_encoders() -> set[str]:
    binary = ffmpeg_bin()
    if not binary:
        return set()
    out = subprocess.run([binary, "-hide_banner", "-encoders"], capture_output=True, text=True,
                         timeout=30).stdout
    return {m[1] for m in re.finditer(r"^\s*[VAS][\w.]{5}\s+(\S+)", out, re.M)}


@lru_cache(maxsize=1)
def list_filters() -> set[str]:
    binary = ffmpeg_bin()
    if not binary:
        return set()
    out = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True,
                         timeout=30).stdout
    return {m[1] for m in re.finditer(r"^\s*[TSC.]{3}\s+(\S+)", out, re.M)}


@lru_cache(maxsize=8)
def encoder_works(encoder: str) -> bool:
    """Being compiled in is not enough (NVENC needs an NVIDIA GPU + driver): do a tiny test encode."""
    if encoder not in list_encoders():
        return False
    try:
        run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=256x256:rate=30:duration=0.2",
                    "-c:v", encoder, "-pix_fmt", "yuv420p", "-f", "null", "-"], timeout=30)
        return True
    except (FFmpegError, subprocess.TimeoutExpired):
        return False


@lru_cache(maxsize=1)
def ffmpeg_major_version() -> int:
    """Major version, or 0 if unknown (e.g. git builds like 'N-112345-g...')."""
    binary = ffmpeg_bin()
    if not binary:
        return 0
    out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=15).stdout
    m = re.search(r"version\s+n?(\d+)\.", out)
    return int(m[1]) if m else 0


def filter_script_args(script: Path) -> list[str]:
    """Load a filter graph from a file: `-/filter_complex` on FFmpeg 7+, legacy option before."""
    major = ffmpeg_major_version()
    if major == 0 or major >= 7:
        return ["-/filter_complex", str(script)]
    return ["-filter_complex_script", str(script)]


HW_H264_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")


def select_video_encoder(preference: str = "auto") -> str:
    """Pick a working H.264 encoder. 'auto' prefers verified hardware, falls back to libx264."""
    if preference != "auto":
        if encoder_works(preference):
            return preference
        log.warning("Requested encoder %s unavailable; falling back to auto", preference)
    for enc in HW_H264_ENCODERS:
        if encoder_works(enc):
            return enc
    if "libx264" in list_encoders():
        return "libx264"
    raise FFmpegError("No usable H.264 encoder found (libx264 missing from this FFmpeg build).")


def encoder_quality_args(encoder: str, crf: int, preset: str) -> list[str]:
    if encoder == "libx264":
        return ["-crf", str(crf), "-preset", preset]
    if encoder == "h264_nvenc":
        return ["-rc", "vbr", "-cq", str(crf), "-preset", "p5"]
    if encoder == "h264_qsv":
        return ["-global_quality", str(crf)]
    if encoder == "h264_amf":
        return ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    return []
