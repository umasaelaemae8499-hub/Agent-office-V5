"""Turns storyboard audio intent into timeline audio cues (the actual mix is rendered
in one FFmpeg graph by app.video.renderer: ducking, loudness normalisation, limiting)."""

from __future__ import annotations

from pathlib import Path

from app.video.timeline import AudioCue


def narration_cues(scene_starts: dict[int, float], lines: list[tuple[int, Path, float]],
                   lead_in: float = 0.3) -> tuple[list[AudioCue], list[str]]:
    """Place each narration line shortly after its scene starts, never overlapping the previous line."""
    cues, warnings, cursor = [], [], 0.0
    for scene_no, wav, dur in lines:
        start = max(scene_starts.get(scene_no, 0.0) + lead_in, cursor + 0.15)
        if start > scene_starts.get(scene_no, 0.0) + 1.5:
            warnings.append(f"narration for scene {scene_no} starts {start:.1f}s - previous line ran long")
        cues.append(AudioCue(source=str(wav), start=round(start, 3), volume=1.0, label=f"narration_s{scene_no}",
                             license="generated locally by TTS"))
        cursor = start + dur
    return cues, warnings
