"""Transition vocabulary -> FFmpeg xfade transition names."""

from __future__ import annotations

from typing import Literal

TransitionType = Literal["cut", "crossfade", "dissolve", "fade_black", "fade_white",
                         "wipe_left", "wipe_right", "slide_up", "slide_left", "circle_open", "zoom_in"]

XFADE_NAME: dict[str, str] = {
    "crossfade": "fade",
    "dissolve": "dissolve",
    "fade_black": "fadeblack",
    "fade_white": "fadewhite",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "slide_up": "slideup",
    "slide_left": "slideleft",
    "circle_open": "circleopen",
    "zoom_in": "zoomin",
}

# Style -> default transition palette used by the planner/editor.
STYLE_TRANSITIONS: dict[str, list[str]] = {
    "cinematic": ["crossfade", "dissolve", "fade_black"],
    "product_commercial": ["crossfade", "zoom_in", "cut", "dissolve"],
    "technology_showcase": ["cut", "slide_left", "crossfade", "wipe_left"],
    "fast_paced": ["cut", "cut", "slide_up", "cut"],
    "documentary": ["dissolve", "cut", "fade_black"],
    "relaxed_storytelling": ["dissolve", "crossfade"],
}


def xfade_name(transition: str) -> str:
    try:
        return XFADE_NAME[transition]
    except KeyError as exc:
        raise ValueError(f"Unknown transition {transition!r}") from exc
