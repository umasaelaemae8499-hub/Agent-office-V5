# AutoCine AI Studio

A local-first, modular video production pipeline. You give it a natural-language brief,
and existing footage or a configured AI generator. It plans, edits, mixes, renders and
validates a finished MP4, then writes a production report.

> **Status:** see [docs/PROGRESS.md](docs/PROGRESS.md) for exactly what is complete, partial and blocked.
> Existing-video -> final-video works end to end. Text-to-video is wired and tested with
> mocked providers, but needs a real generator (ComfyUI workflow or a verified paid API).
> It refuses to fake output.

## Install (Windows 10/11)
1. Python 3.11+ from python.org (tick "Add to PATH").
2. FFmpeg: `winget install Gyan.FFmpeg` (reopen the terminal afterwards).
3. In this folder: `run.bat doctor`. The first run creates `.venv`, installs `requirements.txt`
   (~1 GB with Gradio/OpenCV/librosa) and copies `.env.example` to `.env`.

Optional:
- `pip install pyttsx3` for offline narration with Windows voices.
- `pip install faster-whisper` for transcription. Models download on first use, ~75 MB to 3 GB.
- ComfyUI for local generation (see [docs/COMFYUI.md](docs/COMFYUI.md)).

Linux/macOS: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`, then `python -m app.main ...`.

## Use
```bat
run.bat                                    :: web UI at http://127.0.0.1:7860 (local only)
run.bat doctor                             :: hardware/software report + feature availability
run.bat create --prompt "30-second premium cinematic ad, vertical 9:16" --input C:\clips --music C:\music\licensed.mp3
run.bat create --prompt "Create a 45-second documentary about microplastics with narration"
run.bat plan --prompt "..." --out drafts\x  :: preview plan + storyboard JSON only
run.bat resume --project <id> [--from-stage timeline]
run.bat render --project <id>              :: re-render after hand-editing timeline.json
run.bat validate --project <id>
run.bat approve --project <id> --budget 5  :: only path to paid generation
run.bat list
```

Each project lives in `projects/<id>/`:

| File | Purpose |
|---|---|
| `plan.json`, `storyboard.json/.txt` | validated production plan + editable storyboard |
| `analysis.json`, `frames/` | per-clip metadata, scenes, heuristic scores, keyframes |
| `timeline.json` | the full edit as data. Edit and run `render` to reproduce |
| `output/<name>.mp4` | final video |
| `qc_report.json/.txt` | technical validation |
| `production_report.md/.json` | decisions, assets and licences, costs, limitations |

## How it works
```
brief -> Director (plan) -> Storyboard + prompt/style spec -> Provider router (cache > local > free > paid w/ approval)
      -> generation jobs (SQLite, content-hash cache, bounded retries)        [text_to_video]
      -> ingest (copies/hard-links, originals untouched)                       [video_to_video]
-> Analyzer (ffprobe, PySceneDetect, OpenCV, black/freeze/silence) -> Auto editor (+ librosa beat snapping)
-> Audio (music, TTS narration, SFX library, ducking, EBU R128 loudness) -> Subtitles (SRT/ASS, safe areas)
-> Renderer (single FFmpeg filter graph, one encode) -> QC validator (bounded repair) -> report
```

Key properties:
- **Deterministic timeline.** The edit is JSON. The renderer compiles it into one FFmpeg graph,
  with no intermediate re-encodes.
- **Exact durations.** Transition overlaps are accounted for in planning, editing and rendering.
- **Resumable.** Each stage persists its output, so reruns skip finished stages and reuse generated scenes.
- **Honest.** Clip scores are labelled as technical heuristics. Semantic analysis is reported as not
  performed unless a vision model is plugged in (`app.video.analyzer.VisionDescriber`). An unavailable
  generator blocks the project with options and never substitutes footage.

## Configuration
- `config/settings.yaml`: paths, workers, retries, render quality, analysis weights, UI host/port.
  Any key can be overridden with `AUTOCINE_<SECTION>_<KEY>`.
- `config/providers.yaml`: ComfyUI URL and workflow paths. Cloud entries only name the env var holding the key.
- `.env` (git-ignored): `ALLOW_PAID_GENERATION=false`, `MAX_PROJECT_BUDGET_USD=0`, API keys.
- Music/SFX libraries: put files in `assets/music` / `assets/sfx` and optionally a `library.json`
  with tags and licence info (see `app/audio/sound_effects.py`). Only add media you are licensed to use.

## Security
No `shell=True` anywhere: all subprocesses use argument arrays. Filenames are sanitised (including
Windows reserved names), project paths are checked against traversal, metadata is written atomically,
the UI binds to `127.0.0.1`, and secrets are only read from the environment.

## Tests
```
python -m pytest -q
```
Synthetic media is generated with FFmpeg, so no downloads are needed. The tests label mocked
providers as pipeline/protocol tests, not real generation.
