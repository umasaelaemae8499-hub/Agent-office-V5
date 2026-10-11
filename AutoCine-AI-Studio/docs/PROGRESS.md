# Implementation progress report

Last updated: 2026-10-11. Built and tested in a Linux cloud container (no GPU), **not** on
the target Windows PC. Run `run.bat doctor` on your machine for the real hardware report.

## Environment inspected (build container, not your PC)
Linux x86_64, Python 3.13, FFmpeg 6.1.1 (libx264, libass, all required filters), 4 CPU cores,
15.7 GB RAM, ~29 GB free disk, **no GPU**. NVENC/QSV are compiled in but fail a test encode, so
libx264 is used. No ComfyUI, no cloud API keys. espeak-ng was installed during the build to test narration.

## Phase status
| Phase | Status | Evidence |
|---|---|---|
| 1 Foundation (config, logging, SQLite, doctor, safe paths) | **Complete** | `doctor` runs; tests `test_foundation.py` |
| 2 Automatic editor (import, inspect, timeline, trim, transitions, effects, grading, audio mix, render, QC) | **Complete** | real render validated; `test_video.py` |
| 3 AI Director (plan, storyboard, prompts, style spec) | **Complete (rule-based)** | `test_planning_providers.py`; LLM backend not built (see PLANNING.md) |
| 4 Providers (interface, router, budget gate, jobs, cache, ComfyUI) | **Partial** | ComfyUI adapter tested against a mocked HTTP API only; **Kling/Runway blocked** (API unverified) |
| 5 Smart editing (scene detect, motion/sharpness, keyframes, scoring, beat sync) | **Complete** (heuristic) | `test_editing_audio.py`; semantic vision analysis = hook only, no model |
| 6 Audio & subtitles | **Mostly complete** | music, SFX, ducking, loudness, SRT/ASS and burn-in are done. Narration with subtitles was rendered end to end via espeak-ng (installed during the build). The pyttsx3/Windows SAPI5 and piper backends are **untested**; the faster-whisper transcription path is **untested** (not installed) |
| 7 Web UI | **Complete** | launched locally; Generate endpoint driven with uploads -> rendered MP4 |
| 8 End-to-end validation | **Video->Video PASSED; Text->Video BLOCKED** | see below |

## End-to-end results
- **Existing video -> final video: PASSED.** Run through the CLI with 5 inputs (mixed 16:9/9:16/
  static/black/partly frozen) plus music. Result: 20.00 s, 1080x1920, 30 fps, h264/aac, QC passed.
  The black and frozen segments were rejected automatically and cuts were snapped to beats.
  A narrated, subtitled 16 s edit (espeak-ng voice, 9:16, ASS captions in the safe area) also
  passed QC. Its integrated loudness was -15.2 LUFS against a -14 target, because single-pass
  loudnorm runs about 1 LU low. Also passed
  through the UI (1:1, 10 s). The inputs were synthetic FFmpeg test patterns, not real camera footage.
- **Text prompt -> final video: BLOCKED.** No real generation provider exists in this environment.
  The pipeline stops at `generate` with an explicit reason and options, and produces no fake output.
  The wiring test with a fake provider does **not** count as a Text-to-Video success.

## Acceptance criteria
| # | Criterion | Status |
|---|---|---|
| 1 | Application launches | done (CLI + UI) |
| 2 | Diagnostics work | done |
| 3 | Import existing videos | done |
| 4 | Timeline engine | done |
| 5 | Valid MP4 rendering | done |
| 6 | Audio mixing | done (music/SFX/clip audio/narration/ducking) |
| 7 | Output validation | done |
| 8 | Resume | done (tested) |
| 9 | Config documented | done |
| 10 | Secure credentials | done (.env, git-ignored, no secrets in config) |
| 11 | UI launches locally | done |
| 12 | Automated tests pass | done: 50 passed (narration test skips when no TTS engine is installed) |
| 13 | Real end-to-end edit | done |
| T2V | Real provider + generation + download + edit + validate | **blocked: needs ComfyUI workflow or verified API** |

## Known limitations / next steps
1. On Windows: `run.bat doctor`, then install ComfyUI and export an API workflow if the GPU allows (docs/COMFYUI.md).
2. `pip install pyttsx3` and run a narrated project to verify the TTS path on real hardware.
3. Verify Runway/Kling official API docs, then implement `CloudJobProvider` subclasses.
4. Two-pass loudnorm for exact LUFS targets (currently ~1 LU under target).
5. Speed ramps (variable speed within a shot) are not implemented; constant speed changes are.
6. Optional vision model for semantic clip descriptions (hook: `VisionDescriber`).
7. Optional LLM planning backend for unattended runs (paid, behind the budget gate).
