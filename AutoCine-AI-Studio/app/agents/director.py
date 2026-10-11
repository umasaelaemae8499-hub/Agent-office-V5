"""AI Director: natural-language brief -> validated ProductionPlan.

Planning backends:
  * RuleBasedPlanner - offline, deterministic keyword interpretation. Always available.
  * FilePlanner      - loads a plan JSON authored elsewhere (e.g. by Claude Code during a
                       development session, or edited by hand). Validated like any other plan.
Claude Code is the interactive controller; it is not assumed to be an embeddable runtime API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from app.agents.schemas import AudioPlan, ProductionPlan, StyleSpec
from app.utils.paths import slugify
from app.video.editor import ASPECTS
from app.video.transitions import STYLE_TRANSITIONS


class PlanningBackend(Protocol):
    name: str

    def plan(self, request: str, mode: str, overrides: dict | None = None) -> ProductionPlan: ...


STYLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("documentary", ("documentary", "scientific", "educational", "explainer", "history", "science")),
    ("product_commercial", ("commercial", "advert", " ad ", "product", "promo", "advertising", "brand")),
    ("fast_paced", ("fast-paced", "fast paced", "energetic", "hype", "high energy", "montage")),
    ("technology_showcase", ("technology", "tech ", "futuristic", "showcase", "gadget", "software", "ai ")),
    ("relaxed_storytelling", ("relaxing", "calm", "story", "peaceful", "vlog", "travel")),
    ("cinematic", ("cinematic", "film", "epic", "dramatic")),
]

STRUCTURES: dict[str, list[str]] = {
    "product_commercial": ["hook", "reveal", "detail", "feature", "feature", "hero shot", "closing"],
    "technology_showcase": ["hook", "detail", "feature", "feature", "in action", "finale"],
    "documentary": ["establishing", "context", "process", "process", "impact", "reflection", "conclusion"],
    "fast_paced": ["hook", "action", "action", "action", "action", "climax", "outro"],
    "relaxed_storytelling": ["establishing", "journey", "moment", "moment", "reflection", "ending"],
    "cinematic": ["establishing", "build-up", "reveal", "detail", "climax", "resolution"],
}

STYLE_DEFAULTS: dict[str, dict] = {
    "product_commercial": {"grade": "product_clean", "music": "modern minimal electronic, premium",
                           "lighting": "softbox studio lighting with crisp rim lights", "scene": 4.0,
                           "camera": "smooth motion-control moves, macro details, slow orbits"},
    "technology_showcase": {"grade": "cool", "music": "pulsing synthwave / tech", "scene": 3.5,
                            "lighting": "cool blue key light, neon accents, volumetric haze",
                            "camera": "slow push-ins, orbiting tracking shots"},
    "documentary": {"grade": "documentary", "music": "subtle ambient underscore", "scene": 5.5,
                    "lighting": "natural, motivated lighting with dramatic contrast",
                    "camera": "steady observational shots, slow pans"},
    "fast_paced": {"grade": "high_contrast", "music": "upbeat high-energy", "scene": 2.0,
                   "lighting": "punchy high-contrast lighting", "camera": "dynamic handheld, whip pans"},
    "relaxed_storytelling": {"grade": "warm", "music": "warm acoustic", "scene": 6.0,
                             "lighting": "golden hour natural light", "camera": "gentle gimbal glides"},
    "cinematic": {"grade": "cinematic_teal_orange", "music": "orchestral cinematic", "scene": 5.0,
                  "lighting": "dramatic cinematic lighting, shallow depth of field",
                  "camera": "slow dolly moves, anamorphic framing"},
}

LIGHTING_HINTS = {"neon": "neon accent lighting", "golden": "golden hour light", "blue": "cool blue tones",
                  "dark": "low-key dark lighting", "realistic lighting": "physically realistic lighting",
                  "dramatic lighting": "dramatic high-contrast lighting", "soft": "soft diffused light"}


def parse_duration(text: str, default: float = 30.0) -> float:
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(minutes?|mins?|m\b)", t)
    if m:
        return float(m[1]) * 60
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(seconds?|secs?|s\b)", t)
    return float(m[1]) if m else default


def parse_aspect(text: str, default: str = "16:9") -> str:
    t = text.lower()
    m = re.search(r"\b(9:16|16:9|1:1|4:5)\b", t)
    if m:
        return m[1]
    if any(k in t for k in ("vertical", "tiktok", "shorts", "reels", "portrait", "story")):
        return "9:16"
    if any(k in t for k in ("square", "instagram post")):
        return "1:1"
    return default


def parse_style(text: str) -> str:
    t = f" {text.lower()} "
    for style, kws in STYLE_KEYWORDS:
        if any(k in t for k in kws):
            return style
    return "cinematic"


def extract_topic(text: str) -> str:
    t = text.strip().rstrip(".")
    m = re.search(r"\b(?:showing|about|featuring|of|on)\s+(.+?)(?:,|\.|\bwith\b|\busing\b|$)", t, re.I)
    topic = m[1] if m else t
    topic = re.sub(r"^(an?|the)\s+", "", topic.strip(), flags=re.I)
    return topic[:160]


def select_beats(structure: list[str], n: int) -> list[str]:
    """Keep the opener and closer; sample (n<len) or cycle (n>len) the middle beats."""
    if n == 1:
        return [structure[len(structure) // 2]]
    if n == 2:
        return [structure[0], structure[-1]]
    middle = structure[1:-1] or structure
    if n - 2 <= len(middle):
        picks = [middle[round(i * (len(middle) - 1) / max(1, n - 3))] for i in range(n - 2)] if n > 3 \
            else [middle[len(middle) // 2]]
    else:
        picks = [middle[i % len(middle)] for i in range(n - 2)]
    return [structure[0], *picks, structure[-1]]


class RuleBasedPlanner:
    """Deterministic offline planner. Good structure, templated wording - edit the JSON for craft."""

    name = "rule_based"

    def plan(self, request: str, mode: str = "text_to_video", overrides: dict | None = None) -> ProductionPlan:
        ov = overrides or {}
        low = request.lower()
        style = ov.get("style") or parse_style(request)
        defaults = STYLE_DEFAULTS[style]
        duration = float(ov.get("duration_seconds") or parse_duration(request))
        aspect = ov.get("aspect_ratio") or parse_aspect(request, "9:16" if style == "fast_paced" else "16:9")
        w, h = ASPECTS[aspect]
        topic = ov.get("topic") or extract_topic(request)
        scene_len = defaults["scene"]
        scene_count = int(ov.get("scene_count") or max(1, min(12, round(duration / scene_len))))
        structure = STRUCTURES[style]
        beats = select_beats(structure, scene_count)
        lighting = ", ".join([defaults["lighting"], *[v for k, v in LIGHTING_HINTS.items() if k in low]])
        narration = any(k in low for k in ("narration", "narrator", "voiceover", "voice-over", "voice over"))
        no_music = "no music" in low or "without music" in low
        audio = AudioPlan(
            music=not no_music, music_style=defaults["music"], narration=narration,
            sound_effects=any(k in low for k in ("sound effect", "sfx", "sound design")),
            subtitles=any(k in low for k in ("subtitle", "caption")) or (narration and aspect == "9:16"),
        )
        palette = "deep blacks, cool blues and silver highlights" if style == "technology_showcase" else \
            "warm amber and soft neutrals" if style == "relaxed_storytelling" else \
            "clean neutrals with a single accent colour" if style == "product_commercial" else \
            "natural colours with teal shadows and warm highlights"
        spec = StyleSpec(
            visual_style=f"{style.replace('_', ' ')}, photorealistic, high detail, 4k",
            subject_identity=topic, color_palette=palette, lighting=lighting,
            environment="dark matte studio surface with subtle reflections" if style in
            ("product_commercial", "technology_showcase") else "",
            camera_language=defaults["camera"],
        )
        if "reference_images" in ov:
            spec.reference_images = list(ov["reference_images"])
        trans = STYLE_TRANSITIONS[style]
        plan = ProductionPlan(
            project_name=ov.get("project_name") or slugify(topic, 40), mode=mode, topic=topic,
            duration_seconds=duration, aspect_ratio=aspect, resolution=f"{w}x{h}", fps=int(ov.get("fps", 30)),
            style=style, scene_count=scene_count, narrative_structure=beats,
            transition_preference=trans, transition_duration=0.6 if style != "fast_paced" else 0.3,
            color_grade=defaults["grade"], audio=audio, style_spec=spec, planner=self.name,
            notes=["Rule-based plan: structure and pacing are derived from keywords; scene wording is "
                   "templated. Edit plan.json / storyboard.json (or have Claude Code author them) for "
                   "bespoke creative direction."],
        )
        return plan


class FilePlanner:
    name = "file"

    def __init__(self, path: Path) -> None:
        self.path = path

    def plan(self, request: str, mode: str = "text_to_video", overrides: dict | None = None) -> ProductionPlan:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("mode", mode)
        data.setdefault("planner", "file")
        return ProductionPlan.model_validate(data)


def get_planner(backend: str = "rule_based", plan_file: Path | None = None) -> PlanningBackend:
    if plan_file is not None or backend == "file":
        if plan_file is None:
            raise ValueError("planning backend 'file' requires --plan-file")
        return FilePlanner(plan_file)
    return RuleBasedPlanner()
