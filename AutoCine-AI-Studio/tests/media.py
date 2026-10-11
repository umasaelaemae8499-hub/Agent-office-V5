"""Synthetic test media generated with FFmpeg's lavfi sources (no external downloads)."""

from __future__ import annotations

from pathlib import Path

from app.utils.ffmpeg import run_ffmpeg


def make_clip(path: Path, duration: float = 3.0, size: str = "1280x720", fps: int = 30,
              pattern: str = "testsrc2", tone_hz: int | None = 440) -> Path:
    args = ["-y", "-f", "lavfi", "-i", f"{pattern}=size={size}:rate={fps}:duration={duration}"]
    if tone_hz:
        args += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={duration}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-shortest"] if tone_hz else ["-an"]
    args.append(str(path))
    run_ffmpeg(args)
    return path


def make_black_clip(path: Path, duration: float = 2.0, size: str = "640x360") -> Path:
    run_ffmpeg(["-y", "-f", "lavfi", "-i", f"color=c=black:size={size}:rate=30:duration={duration}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)])
    return path


def make_frozen_then_moving(path: Path) -> Path:
    """2s static image + 2s moving pattern, to exercise freeze detection."""
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=30:duration=2",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)])
    return path


def make_music(path: Path, duration: float = 12.0, bpm: int = 120) -> Path:
    """A click track at *bpm* layered over a pad: gives librosa clear beats."""
    period = 60.0 / bpm
    expr = f"0.8*sin(2*PI*1000*t)*exp(-60*mod(t\\,{period:.6f}))+0.1*sin(2*PI*220*t)"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={duration}",
                "-c:a", "pcm_s16le", str(path)])
    return path


def make_voice_like(path: Path, duration: float = 3.0) -> Path:
    """Amplitude-modulated tone standing in for narration (tests ducking/mixing, not speech)."""
    run_ffmpeg(["-y", "-f", "lavfi", "-i",
                f"aevalsrc='0.6*sin(2*PI*300*t)*(0.5+0.5*sin(2*PI*3*t))':s=48000:d={duration}",
                "-c:a", "pcm_s16le", str(path)])
    return path


def make_image(path: Path, size: str = "1024x1024") -> Path:
    run_ffmpeg(["-y", "-f", "lavfi", "-i", f"mandelbrot=size={size}:rate=1", "-frames:v", "1", str(path)])
    return path
