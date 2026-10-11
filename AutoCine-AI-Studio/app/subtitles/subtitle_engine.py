"""Subtitles: SRT/ASS writing from timed text, optional faster-whisper transcription,
style presets with platform safe areas (9:16 captions sit above the UI zone)."""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def split_long_cues(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> list[Cue]:
    """Break long text into readable chunks, sharing the cue's time proportionally."""
    out: list[Cue] = []
    for c in cues:
        lines = textwrap.wrap(c.text, max_chars) or [""]
        chunks = [" ".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]
        total = sum(len(x) for x in chunks) or 1
        t = c.start
        for ch in chunks:
            d = (c.end - c.start) * len(ch) / total
            out.append(Cue(round(t, 3), round(t + d, 3), ch))
            t += d
    return out


def write_srt(cues: list[Cue], path: Path, max_chars: int = 42) -> Path:
    blocks = []
    for i, c in enumerate(split_long_cues(cues, max_chars), 1):
        text = "\n".join(textwrap.wrap(c.text, max_chars))
        blocks.append(f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


# name -> (font size as fraction of video height, bold, primary colour &HBBGGRR, outline, box)
STYLES = {
    "minimal_documentary": dict(size=0.034, bold=0, colour="&H00FFFFFF", outline=1.5, box=False, upper=False),
    "modern_cinematic": dict(size=0.040, bold=0, colour="&H00F0F0F0", outline=0, box=True, upper=False),
    "social_media": dict(size=0.050, bold=1, colour="&H00FFFFFF", outline=4, box=False, upper=True),
    "bold_animated": dict(size=0.058, bold=1, colour="&H0000E6FF", outline=5, box=False, upper=True),
}


def write_ass(cues: list[Cue], path: Path, width: int, height: int, style: str = "social_media") -> Path:
    st = STYLES.get(style, STYLES["social_media"])
    vertical = height > width
    font_size = int(height * st["size"] * (0.75 if vertical else 1.0))
    # Safe areas: vertical platforms overlay UI on the bottom ~20% and right edge.
    margin_v = int(height * (0.22 if vertical else 0.07))
    margin_lr = int(width * (0.08 if vertical else 0.05))
    max_chars = 26 if vertical else 42
    border_style = 3 if st["box"] else 1
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{st['colour']},&H000000FF,&H00000000,&H80000000,{st['bold']},0,0,0,100,100,0,0,{border_style},{st['outline']},0,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in split_long_cues(cues, max_chars):
        text = c.text.upper() if st["upper"] else c.text
        text = "\\N".join(textwrap.wrap(text.replace("{", "(").replace("}", ")"), max_chars))
        if style == "bold_animated":  # pop-in scale animation
            text = r"{\fscx80\fscy80\t(0,120,\fscx100\fscy100)}" + text
        events.append(f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def cues_from_narration(placements: list[tuple[float, float, str]]) -> list[Cue]:
    """(start, duration, text) from actual synthesized audio durations -> accurate cues."""
    return [Cue(s, s + d, t) for s, d, t in placements]


def transcribe(media: Path, model_size: str = "small", language: str | None = None,
               word_timestamps: bool = False) -> list[Cue]:
    """Speech -> cues with faster-whisper (optional dependency; model downloads on first use)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("faster-whisper not installed: pip install faster-whisper") from e
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, _info = model.transcribe(str(media), language=language, word_timestamps=word_timestamps,
                                       vad_filter=True)
    return [Cue(round(s.start, 3), round(s.end, 3), s.text.strip()) for s in segments]
