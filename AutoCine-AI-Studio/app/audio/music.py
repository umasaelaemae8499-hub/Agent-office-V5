"""Music analysis (tempo, beats, onsets) and style-aware beat snapping of cut points."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class MusicAnalysis:
    path: str
    duration: float
    tempo_bpm: float
    beats: list[float]
    downbeats: list[float]                 # approx: every 4th beat from the strongest phase
    onset_peaks: list[float] = field(default_factory=list)
    energy_curve: list[float] = field(default_factory=list)  # RMS per second, 0..1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_music(path: Path, sr: int = 22050) -> MusicAnalysis:
    import librosa

    y, sr = librosa.load(str(path), sr=sr, mono=True)
    duration = float(len(y) / sr)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="frames")
    tempo = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr).round(3).tolist()
    # Downbeat phase: of the 4 possible phases, pick the one with the strongest onsets.
    downbeats: list[float] = []
    if len(beat_frames) >= 4:
        strengths = [float(onset_env[beat_frames[p::4]].mean()) for p in range(4)]
        phase = int(np.argmax(strengths))
        downbeats = [beats[i] for i in range(phase, len(beats), 4)]
    peaks = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
    rms = librosa.feature.rms(y=y)[0]
    frames_per_sec = sr / 512
    per_sec = [float(rms[int(i * frames_per_sec):int((i + 1) * frames_per_sec)].mean())
               for i in range(int(duration)) if int(i * frames_per_sec) < len(rms)]
    mx = max(per_sec) if per_sec and max(per_sec) > 0 else 1.0
    return MusicAnalysis(path=str(path), duration=round(duration, 3), tempo_bpm=round(tempo, 2), beats=beats,
                         downbeats=downbeats, onset_peaks=np.round(peaks, 3).tolist(),
                         energy_curve=[round(v / mx, 3) for v in per_sec])


# How strongly each editing style follows the music. tolerance = max seconds a cut may move.
SYNC_PROFILES: dict[str, dict[str, Any]] = {
    "fast_paced": {"strength": 1.0, "tolerance": 0.35, "grid": "beats"},
    "product_commercial": {"strength": 0.85, "tolerance": 0.4, "grid": "beats"},
    "technology_showcase": {"strength": 0.8, "tolerance": 0.4, "grid": "beats"},
    "cinematic": {"strength": 0.6, "tolerance": 0.6, "grid": "downbeats"},
    "documentary": {"strength": 0.3, "tolerance": 0.5, "grid": "downbeats"},
    "relaxed_storytelling": {"strength": 0.25, "tolerance": 0.6, "grid": "downbeats"},
}


def snap_cuts(cut_times: list[float], music: MusicAnalysis, style: str,
              min_gap: float = 0.6) -> list[float]:
    """Move interior cut points toward nearby beats.

    A cut is snapped only if a grid point is within the style's tolerance, and only
    with probability-free deterministic gating by `strength`: cuts are ranked by how
    close they already are, and only the closest `strength` fraction get snapped, so
    low-strength styles keep most cuts where the edit put them.
    """
    prof = SYNC_PROFILES.get(style, SYNC_PROFILES["cinematic"])
    grid = np.array(music.downbeats if prof["grid"] == "downbeats" and len(music.downbeats) >= 2 else music.beats)
    if len(grid) == 0 or len(cut_times) <= 2:
        return list(cut_times)
    interior = list(range(1, len(cut_times) - 1))
    candidates = []
    for i in interior:
        j = int(np.argmin(np.abs(grid - cut_times[i])))
        dist = abs(grid[j] - cut_times[i])
        if dist <= prof["tolerance"]:
            candidates.append((dist, i, float(grid[j])))
    candidates.sort()
    n_snap = int(round(len(interior) * prof["strength"]))
    out = list(cut_times)
    for _, i, target in candidates[:n_snap]:
        if target - out[i - 1] >= min_gap and cut_times[i + 1] - target >= min_gap:
            out[i] = round(target, 3)
    return out
