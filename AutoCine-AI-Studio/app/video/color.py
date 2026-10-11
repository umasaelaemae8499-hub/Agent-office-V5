"""Colour-correction / grading presets as FFmpeg filter chains."""

from __future__ import annotations

COLOR_PRESETS: dict[str, str] = {
    "none": "",
    "neutral": "eq=contrast=1.03:saturation=1.03",
    "cinematic_teal_orange": "colorbalance=rs=-0.06:bs=0.08:rh=0.08:bh=-0.06,eq=contrast=1.08:saturation=1.08",
    "warm": "colorbalance=rm=0.06:bm=-0.05:rh=0.04,eq=saturation=1.08",
    "cool": "colorbalance=bm=0.06:rm=-0.04:bh=0.04,eq=saturation=1.02",
    "high_contrast": "eq=contrast=1.18:brightness=-0.02:saturation=1.10",
    "documentary": "eq=contrast=1.02:saturation=0.92",
    "product_clean": "eq=contrast=1.06:brightness=0.02:saturation=1.05,unsharp=5:5:0.4",
    "moody_dark": "eq=contrast=1.12:brightness=-0.05:saturation=0.85,colorbalance=bs=0.05",
}


def color_filter(preset: str) -> str:
    if preset not in COLOR_PRESETS:
        raise ValueError(f"Unknown colour preset {preset!r}; options: {sorted(COLOR_PRESETS)}")
    return COLOR_PRESETS[preset]


def auto_level_filter(mean_brightness: float, target: float = 0.45) -> str:
    """Gentle exposure correction from a measured mean luma (0..1). Clamped to avoid crushing."""
    if mean_brightness <= 0:
        return ""
    delta = max(-0.12, min(0.12, (target - mean_brightness) * 0.5))
    return f"eq=brightness={delta:.3f}" if abs(delta) > 0.015 else ""
