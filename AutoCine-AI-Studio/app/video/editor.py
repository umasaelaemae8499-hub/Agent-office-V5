"""Automatic editing decisions: analysed footage (+ optional music) -> Timeline.

Deterministic: same inputs and settings always yield the same timeline JSON.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.audio.music import MusicAnalysis, snap_cuts
from app.video.analyzer import ClipAnalysis, Segment
from app.video.timeline import (Effects, MusicTrack, OutputSpec, Timeline, Transition, VideoClip,
                                fit_durations_with_caps)
from app.video.transitions import STYLE_TRANSITIONS

# Editing style -> pacing / look
STYLE_PROFILES: dict[str, dict] = {
    "cinematic": {"shot": 3.5, "trans": 0.8, "color": "cinematic_teal_orange", "vignette": True, "hook_first": False},
    "product_commercial": {"shot": 2.5, "trans": 0.5, "color": "product_clean", "vignette": False, "hook_first": True},
    "technology_showcase": {"shot": 2.0, "trans": 0.4, "color": "cool", "vignette": False, "hook_first": True},
    "fast_paced": {"shot": 1.2, "trans": 0.3, "color": "high_contrast", "vignette": False, "hook_first": True},
    "documentary": {"shot": 4.5, "trans": 0.7, "color": "documentary", "vignette": False, "hook_first": False},
    "relaxed_storytelling": {"shot": 5.0, "trans": 1.0, "color": "warm", "vignette": True, "hook_first": False},
}

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def resolution_for(aspect: str, custom: tuple[int, int] | None = None) -> tuple[int, int]:
    if custom:
        return custom
    if aspect not in ASPECTS:
        raise ValueError(f"Unsupported aspect ratio {aspect}; use one of {list(ASPECTS)} or a custom size")
    return ASPECTS[aspect]


@dataclass
class _Shot:
    path: str
    seg: Segment
    order: tuple[int, float]


def _usable(seg: Segment, ca: ClipAnalysis, min_len: float) -> bool:
    if seg.duration < min_len or seg.score < 0.15:
        return False
    bad = sum(max(0.0, min(seg.end, e) - max(seg.start, s)) for s, e in ca.black_intervals + ca.freeze_intervals)
    return bad < 0.5 * seg.duration


def build_edit_timeline(analyses: list[ClipAnalysis], target_duration: float, style: str = "cinematic",
                        aspect: str = "9:16", music: MusicAnalysis | None = None,
                        music_path: str | None = None, fps: float = 30,
                        output_overrides: dict | None = None) -> tuple[Timeline, list[str]]:
    """Return (timeline, decision_log)."""
    prof = STYLE_PROFILES.get(style, STYLE_PROFILES["cinematic"])
    notes: list[str] = []
    W, H = resolution_for(aspect)
    shots: list[_Shot] = []
    for fi, ca in enumerate(analyses):
        for seg in ca.segments:
            if _usable(seg, ca, min_len=0.8):
                shots.append(_Shot(ca.path, seg, (fi, seg.start)))
            else:
                notes.append(f"skipped {Path(ca.path).name} {seg.start:.1f}-{seg.end:.1f}s "
                             f"(score {seg.score:.2f}, too short/black/frozen)")
    if not shots:
        raise ValueError("No usable footage: every segment was too short, black or frozen.")

    n_target = max(1, round(target_duration / prof["shot"]))
    per_file_cap = math.ceil(n_target / max(1, len(analyses))) + 1
    ranked = sorted(shots, key=lambda s: (-s.seg.score, s.order))
    chosen: list[_Shot] = []
    counts: dict[str, int] = {}
    for s in ranked:
        if len(chosen) >= n_target:
            break
        if counts.get(s.path, 0) < per_file_cap:
            chosen.append(s)
            counts[s.path] = counts.get(s.path, 0) + 1
    # Not enough distinct segments: split the longest chosen segments into sub-shots.
    while len(chosen) < n_target:
        longest = max(chosen, key=lambda s: s.seg.duration)
        if longest.seg.duration < 2 * prof["shot"] * 0.6:
            break
        mid = (longest.seg.start + longest.seg.end) / 2
        a = Segment(**{**longest.seg.__dict__, "end": mid})
        b = Segment(**{**longest.seg.__dict__, "start": mid})
        chosen.remove(longest)
        chosen += [_Shot(longest.path, a, (longest.order[0], a.start)), _Shot(longest.path, b, (longest.order[0], b.start))]
    # Order: chronological (source order, then time); optionally lead with the strongest shot as a hook.
    chosen.sort(key=lambda s: s.order)
    if prof["hook_first"] and len(chosen) > 2:
        best = max(chosen, key=lambda s: s.seg.score)
        chosen.remove(best)
        chosen.insert(0, best)
        notes.append(f"opened with highest-scoring shot ({Path(best.path).name} @ {best.seg.start:.1f}s) as hook")

    def min_speed(sh: _Shot) -> float:
        return 0.2 if sh.seg.motion < 0.005 else 0.5   # stills can be held; moving footage max 2x slow-mo

    def cap(sh: _Shot) -> float:
        return max(0.3, (sh.seg.duration - 0.1) / min_speed(sh))

    palette = STYLE_TRANSITIONS.get(style, ["crossfade"])

    def transitions_for(n: int) -> tuple[list[str], list[float]]:
        types = ["cut"] + [palette[i % len(palette)] for i in range(n - 1)]
        return types, [0.0 if t == "cut" else prof["trans"] for t in types]

    # Reuse footage (cycling the selection) only if the material genuinely cannot fill the target.
    trans_types, trans_d = transitions_for(len(chosen))
    base = list(chosen)
    while sum(cap(c) for c in chosen) - sum(trans_d[1:]) < 1.15 * target_duration and len(chosen) < 4 * len(base) + 4:
        chosen.append(base[len(chosen) % len(base)])
        trans_types, trans_d = transitions_for(len(chosen))
    if len(chosen) > len(base):
        notes.append(f"footage too short for {target_duration:.1f}s: reused {len(chosen) - len(base)} shot(s)")
    caps = [cap(c) for c in chosen]
    durs = fit_durations_with_caps([prof["shot"]] * len(chosen), trans_d, target_duration, caps)

    if music is not None:
        starts, t = [], 0.0
        for i, d in enumerate(durs):
            t -= trans_d[i] if i else 0.0
            starts.append(t)
            t += d
        bounds = starts + [t]
        snapped = snap_cuts(bounds, music, style)
        moved = sum(1 for a, b in zip(bounds, snapped) if abs(a - b) > 1e-3)
        new_durs = []
        for i in range(len(durs)):
            nxt_overlap = trans_d[i + 1] if i + 1 < len(durs) else 0.0
            new_durs.append(min(caps[i], round(snapped[i + 1] - snapped[i] + nxt_overlap, 3)))
        # Any residual from clamping is absorbed by the final shot so earlier beat-cuts stay put.
        residual = target_duration + sum(trans_d[1:]) - sum(new_durs)
        new_durs[-1] = round(min(caps[-1], new_durs[-1] + residual), 3)
        # Still short? Extend shots with spare footage, from the end backwards.
        for i in reversed(range(len(new_durs))):
            residual = target_duration + sum(trans_d[1:]) - sum(new_durs)
            if residual <= 1e-4:
                break
            new_durs[i] = round(min(caps[i], new_durs[i] + residual), 3)
        if moved:
            notes.append(f"snapped {moved} cut(s) to music beats ({music.tempo_bpm:.0f} BPM, style={style})")
        durs = new_durs

    clips: list[VideoClip] = []
    for i, (shot, dur) in enumerate(zip(chosen, durs)):
        seg = shot.seg
        avail = seg.duration - 0.1
        speed = 1.0 if avail >= dur else max(min_speed(shot), round(avail / dur, 4))
        if speed < 1.0 and seg.motion >= 0.005:
            notes.append(f"shot {i} slowed to {speed:.2f}x to fill {dur:.2f}s")
        src_len = min(avail, dur * speed)
        dur = src_len / speed
        centre = (seg.start + seg.end) / 2
        in_p = max(seg.start + 0.05, centre - src_len / 2)
        in_p = max(0.0, min(in_p, seg.end - 0.05 - src_len))
        static = seg.motion < 0.01
        zoom = (0.10 if i % 2 == 0 else -0.10) if static else 0.0
        fx = Effects(zoom=zoom, vignette=prof["vignette"], auto_level=seg.brightness)
        clips.append(VideoClip(source=shot.path, in_point=round(in_p, 4), out_point=round(in_p + src_len, 4),
                               speed=speed, effects=fx, color=prof["color"],
                               transition_in=Transition(type=trans_types[i], duration=trans_d[i]),
                               label=f"shot{i:02d}"))
    # A shortened shot may make a transition too long: degrade gracefully to a cut.
    for prev, cur in zip(clips, clips[1:]):
        if cur.transition_in.duration >= min(prev.duration, cur.duration):
            cur.transition_in = Transition(type="cut")
    out = OutputSpec(width=W, height=H, fps=fps, fade_in_from_black=0.4, fade_out_to_black=0.8,
                     **(output_overrides or {}))
    mt = MusicTrack(source=music_path, volume=0.5) if music_path else None
    tl = Timeline(clips=clips, music=mt, output=out, clip_audio_volume=0.25 if music_path else 1.0)
    notes.append(f"{len(clips)} shots, style={style}, {W}x{H}, timeline {tl.total_duration:.2f}s "
                 f"(target {target_duration:.2f}s)")
    return tl, notes
