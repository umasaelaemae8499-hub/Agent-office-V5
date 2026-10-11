# Local generation with ComfyUI

AutoCine drives ComfyUI through its HTTP API (`/system_stats`, `/prompt`, `/history/{id}`,
`/view`, `/upload/image`). It does **not** hard-code a model graph: you build and test a
workflow in ComfyUI for a model your GPU can actually run, then hand it to AutoCine.

## Steps
1. Install ComfyUI (Windows portable build is simplest) and start it (default `http://127.0.0.1:8188`).
2. Build a workflow that produces a video (or image) and run it once in the ComfyUI UI.
3. Enable dev mode options, then **Save (API Format)**.
4. In the saved JSON, replace literal values with placeholders:

| Placeholder | Type | Meaning |
|---|---|---|
| `{{PROMPT}}` | string | composed scene prompt (with project style spec) |
| `{{NEGATIVE_PROMPT}}` | string | negative prompt |
| `{{WIDTH}}`, `{{HEIGHT}}` | int | generation size (smaller than delivery size; renderer upscales) |
| `{{FRAMES}}`, `{{FPS}}`, `{{DURATION}}` | int/int/float | clip length |
| `{{SEED}}` | int | per-scene deterministic seed |
| `{{REFERENCE_IMAGE}}` | string | uploaded reference image name (image-to-video / consistency) |

   A value that is *exactly* a placeholder keeps its type (ints stay ints).
5. Save as `config/workflows/video_workflow_api.json` (and/or `image_workflow_api.json`).
6. Run `python -m app.main doctor`; `text_to_video` should read `available (ComfyUI local)`.

If only an image workflow exists, AutoCine generates one AI still per scene and animates it
with a camera move. This is reported per scene in the production report, so it never passes
as real video generation.

`config/workflows/examples/image_sd_api_example.json` is a core-node text-to-image example.
It has **not** been run against a live ComfyUI in this repository.

## Hardware guidance (approximate; verify against each model's own page)
| VRAM | Realistic local choice | Typical download |
|---|---|---|
| none / <6 GB | no local generation; edit existing footage | - |
| 8 GB | SD1.5 / SDXL-Turbo stills -> animated stills; video models very slow if they fit at all | 2-7 GB |
| 12-16 GB | SDXL / FLUX-schnell (fp8) stills; small video models (Wan 2.x 1.3B, LTX-Video) for short clips | 6-20 GB |
| 24 GB+ | larger video models (e.g. Wan 2.x 14B fp8, HunyuanVideo) | 15-40 GB |

Never assume a model fits: run it once in ComfyUI first. Generation of a 5 s clip can take
minutes to tens of minutes locally.
