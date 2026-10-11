"""Storyboard agent: ProductionPlan -> Storyboard (durations reconcile with transition overlaps)."""

from __future__ import annotations

from app.agents.prompt_engine import apply_prompts
from app.agents.schemas import ProductionPlan, Scene, Storyboard
from app.video.timeline import fit_durations_to_target

BEAT_TEMPLATES: dict[str, dict[str, str]] = {
    "hook": {"desc": "Striking opening image of {t}, partially revealed in silhouette", "cam": "extreme close-up",
             "move": "fast push-in", "sfx": "deep whoosh impact"},
    "reveal": {"desc": "{t} is revealed in full, slowly rotating", "cam": "three-quarter view",
               "move": "slow orbit", "sfx": "rising shimmer"},
    "detail": {"desc": "Macro detail of {t}, highlighting texture and craftsmanship", "cam": "macro close-up",
               "move": "slow lateral slide", "sfx": "subtle mechanical clicks"},
    "feature": {"desc": "{t} demonstrating a key feature, components moving with precision",
                "cam": "medium shot", "move": "smooth tracking shot", "sfx": "soft mechanical movement"},
    "hero shot": {"desc": "Hero shot of {t}, perfectly lit, centred in frame", "cam": "low angle",
                  "move": "slow dolly out", "sfx": "cinematic hit"},
    "closing": {"desc": "{t} resting in final position with a clean negative-space composition",
                "cam": "wide shot", "move": "static", "sfx": "soft tonal resolve"},
    "in action": {"desc": "{t} in use in a realistic setting", "cam": "over-the-shoulder",
                  "move": "handheld follow", "sfx": "ambient room tone"},
    "finale": {"desc": "Final dramatic composition of {t} with light sweeping across it", "cam": "wide shot",
               "move": "slow push-in", "sfx": "final impact and reverb tail"},
    "establishing": {"desc": "Establishing view introducing {t}", "cam": "wide shot", "move": "slow aerial glide",
                     "sfx": "ambient atmosphere"},
    "context": {"desc": "Context: the world around {t}", "cam": "medium wide", "move": "slow pan",
                "sfx": "ambient atmosphere"},
    "process": {"desc": "Step-by-step visualisation of how {t} unfolds, scientifically accurate",
                "cam": "medium close-up", "move": "slow push-in", "sfx": "subtle organic textures"},
    "impact": {"desc": "The consequences of {t}, shown with dramatic clarity", "cam": "close-up",
               "move": "slow dolly in", "sfx": "low drone"},
    "reflection": {"desc": "Quiet reflective image related to {t}", "cam": "wide shot", "move": "static",
                   "sfx": "soft ambience"},
    "conclusion": {"desc": "Concluding image summarising {t}", "cam": "wide shot", "move": "slow pull back",
                   "sfx": "gentle resolve"},
    "action": {"desc": "High-energy moment featuring {t}", "cam": "dynamic angle", "move": "whip pan",
               "sfx": "punchy whoosh"},
    "climax": {"desc": "Peak moment of {t}, maximum energy", "cam": "low angle", "move": "rapid push-in",
               "sfx": "big impact"},
    "outro": {"desc": "Outro shot of {t}", "cam": "wide shot", "move": "pull back", "sfx": "tail whoosh"},
    "journey": {"desc": "Journey through the world of {t}", "cam": "tracking shot", "move": "gimbal glide",
                "sfx": "footsteps and ambience"},
    "moment": {"desc": "An intimate moment with {t}", "cam": "medium close-up", "move": "gentle drift",
               "sfx": "soft ambience"},
    "ending": {"desc": "Peaceful ending image of {t}", "cam": "wide shot", "move": "slow fade-out drift",
               "sfx": "soft ambience"},
    "build-up": {"desc": "Tension builds around {t}", "cam": "medium shot", "move": "slow push-in",
                 "sfx": "rising tension"},
    "resolution": {"desc": "Resolution: {t} in calm final composition", "cam": "wide shot",
                   "move": "slow pull back", "sfx": "resolving tone"},
}

# Templated placeholder narration; replace with a written script for production quality.
NARRATION_TEMPLATES: dict[str, list[str]] = {
    "establishing": ["This is the story of {t}."],
    "context": ["To understand it, we need to look closer.", "It is all around us."],
    "process": ["Step by step, the process unfolds.", "Little by little, it moves deeper.",
                "And it does not stop there."],
    "impact": ["The impact is greater than it seems."],
    "reflection": ["What does this mean for us?"],
    "conclusion": ["And that is the story of {t}."],
    "hook": ["Meet {t}."], "reveal": ["Designed without compromise."],
    "detail": ["Every detail matters.", "Precision in every part."],
    "feature": ["Built to perform.", "Engineered for more.", "Made to impress."],
    "hero shot": ["This is {t}."], "closing": ["{t}. Available now."],
}


def build_storyboard(plan: ProductionPlan) -> Storyboard:
    n = plan.scene_count
    beats = plan.narrative_structure or ["hero shot"] * n
    trans_types = ["cut"] + [plan.transition_preference[i % len(plan.transition_preference)] for i in range(n - 1)]
    trans_d = [0.0 if t == "cut" else plan.transition_duration for t in trans_types]
    base = plan.duration_seconds / n
    # Openers and closers slightly longer; durations then fitted so the render is exact.
    weights = [1.15 if i in (0, n - 1) and n > 2 else 1.0 for i in range(n)]
    durs = fit_durations_to_target([base * w for w in weights], trans_d, plan.duration_seconds, min_clip=0.8)
    for i in range(1, n):  # keep transitions shorter than half of each adjacent scene
        trans_d[i] = min(trans_d[i], durs[i] / 2.5, durs[i - 1] / 2.5)
    durs = fit_durations_to_target(durs, trans_d, plan.duration_seconds, min_clip=0.8)
    t = plan.topic
    scenes = []
    seen: dict[str, int] = {}
    for i in range(n):
        beat = beats[i] if i < len(beats) else beats[-1]
        tpl = BEAT_TEMPLATES.get(beat, BEAT_TEMPLATES["hero shot"])
        sfx = [tpl["sfx"]] if plan.audio.sound_effects else []
        narration = None
        if plan.audio.narration and beat in NARRATION_TEMPLATES:
            variants = NARRATION_TEMPLATES[beat]
            k = seen.get(beat, 0)
            seen[beat] = k + 1
            narration = variants[k % len(variants)].format(t=t) if k < len(variants) else None
            if narration:
                narration = narration[0].upper() + narration[1:]
        scenes.append(Scene(
            scene_number=i + 1, beat=beat, description=tpl["desc"].format(t=t),
            camera_angle=tpl["cam"], camera_movement=tpl["move"], lighting=plan.style_spec.lighting,
            environment=plan.style_spec.environment, subject=plan.style_spec.subject_identity,
            duration=durs[i], generation_duration=5.0 if durs[i] <= 5.0 else 10.0,
            transition_in=trans_types[i], transition_duration=round(trans_d[i], 3),
            sound_effects=sfx, narration=narration,
        ))
    apply_prompts(scenes, plan.style_spec)
    return Storyboard(project_name=plan.project_name, total_duration=plan.duration_seconds, scenes=scenes)
