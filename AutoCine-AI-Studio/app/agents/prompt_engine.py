"""Scene prompt composition with a project-level StyleSpec for visual consistency.

Text prompts alone do not guarantee consistency between generations; when a provider
supports reference images, recurring subjects should be generated with one.
"""

from __future__ import annotations

from app.agents.schemas import Scene, StyleSpec

MAX_PROMPT_CHARS = 1500


def compose_prompt(scene: Scene, spec: StyleSpec) -> str:
    parts = [
        scene.description,
        f"Subject: {spec.subject_identity}" if spec.subject_identity else "",
        f"Materials: {spec.materials}" if spec.materials else "",
        f"Environment: {scene.environment or spec.environment}" if (scene.environment or spec.environment) else "",
        f"Camera: {scene.camera_angle}, {scene.camera_movement}",
        f"Lighting: {scene.lighting or spec.lighting}",
        f"Colour palette: {spec.color_palette}" if spec.color_palette else "",
        f"Style: {spec.visual_style}",
    ]
    prompt = ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip()) + "."
    return prompt[:MAX_PROMPT_CHARS]


def compose_negative(scene: Scene, spec: StyleSpec) -> str:
    extra = scene.negative_prompt.strip()
    return ", ".join(x for x in (spec.negative_prompt, extra) if x)


def apply_prompts(scenes: list[Scene], spec: StyleSpec) -> list[Scene]:
    for s in scenes:
        s.visual_prompt = compose_prompt(s, spec)
        s.negative_prompt = compose_negative(s, spec)
        if spec.reference_images and not s.reference_image:
            s.reference_image = spec.reference_images[0]
    return scenes
