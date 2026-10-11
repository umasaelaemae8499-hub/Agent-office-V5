"""Production pipeline: resumable stages for both production modes.

  text_to_video : plan -> storyboard -> generate -> analyze -> timeline -> audio -> render -> validate -> report
  video_to_video: plan -> ingest -> analyze -> timeline -> audio -> render -> validate -> report

Every stage persists its output (JSON docs + SQLite state). Re-running skips stages
already marked done, and generation reuses cached scene assets by content hash.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agents.director import get_planner
from app.agents.schemas import ProductionPlan, Storyboard
from app.agents.storyboard import build_storyboard
from app.audio.mixer import narration_cues
from app.audio.music import analyze_music
from app.audio.narration import get_tts_backend, synthesize_lines
from app.audio.sound_effects import load_library, match, pick_music
from app.config import Settings
from app.database.storage import ProjectStore
from app.providers.base import GenerationRequest
from app.providers.router import ProviderRouter
from app.quality.validator import Expectations, validate_file
from app.subtitles.subtitle_engine import cues_from_narration, write_ass, write_srt
from app.utils import ffmpeg as ff
from app.utils.paths import VIDEO_EXTENSIONS, atomic_write_text, file_sha256, sanitize_filename
from app.video.analyzer import ClipAnalysis, analyze_clip
from app.video.editor import build_edit_timeline
from app.video.renderer import render
from app.video.timeline import AudioCue, Effects, MusicTrack, OutputSpec, SubtitleTrack, Timeline, Transition, VideoClip

log = logging.getLogger(__name__)
ProgressFn = Callable[[str, str, float], None]   # (stage, message, fraction)


class PipelineBlocked(RuntimeError):
    """Raised when a stage cannot proceed without user action (provider, budget, input)."""


class Orchestrator:
    def __init__(self, settings: Settings, store: ProjectStore, router: ProviderRouter | None = None,
                 progress: ProgressFn | None = None) -> None:
        self.s = settings
        self.store = store
        self.router = router or ProviderRouter(settings, store)
        self.progress = progress or (lambda stage, msg, frac: None)

    # ------------------------------------------------------------ project creation
    def create_project(self, request: str, mode: str = "text_to_video", inputs: list[Path] | None = None,
                       overrides: dict | None = None, plan_file: Path | None = None,
                       exec_mode: str | None = None, budget_usd: float | None = None,
                       music: Path | None = None, narration_script: str | None = None) -> str:
        exec_mode = exec_mode or self.s.execution.mode
        budget = self.s.budget.max_project_budget_usd if budget_usd is None else budget_usd
        pid = self.store.create_project(request, mode, name=(overrides or {}).get("project_name"),
                                        exec_mode=exec_mode, budget_usd=budget)
        self.store.save_doc(pid, "request", {
            "request": request, "mode": mode, "overrides": overrides or {},
            "plan_file": str(plan_file) if plan_file else None,
            "inputs": [str(Path(p).resolve()) for p in (inputs or [])],
            "music": str(Path(music).resolve()) if music else None, "narration_script": narration_script,
        })
        self.store.log_event(pid, f"project created ({mode}, {exec_mode})")
        return pid

    # ------------------------------------------------------------ driver
    def run(self, project_id: str, from_stage: str | None = None) -> dict[str, Any]:
        return asyncio.run(self.arun(project_id, from_stage))

    async def arun(self, project_id: str, from_stage: str | None = None) -> dict[str, Any]:
        proj = self.store.require_project(project_id)
        if from_stage:
            self.store.reset_stages_from(project_id, from_stage)
        stages = (["plan", "storyboard", "generate", "analyze", "timeline", "audio", "render", "validate", "report"]
                  if proj["mode"] == "text_to_video" else
                  ["plan", "ingest", "analyze", "timeline", "audio", "render", "validate", "report"])
        self.store.update_project(project_id, status="running")
        for i, stage in enumerate(stages):
            if self.store.stage_done(project_id, stage):
                self.progress(stage, "already complete - reused", (i + 1) / len(stages))
                continue
            self.store.set_stage(project_id, stage, "running")
            self.progress(stage, "running", i / len(stages))
            try:
                detail = await getattr(self, f"_stage_{stage}")(project_id)
            except PipelineBlocked as e:
                self.store.set_stage(project_id, stage, "blocked", str(e))
                self.store.update_project(project_id, status="blocked")
                self.store.log_event(project_id, str(e), "warning", stage)
                self.progress(stage, f"BLOCKED: {e}", i / len(stages))
                return {"project_id": project_id, "status": "blocked", "stage": stage, "reason": str(e)}
            except Exception as e:
                log.exception("stage %s failed", stage)
                self.store.set_stage(project_id, stage, "failed", f"{e.__class__.__name__}: {e}")
                self.store.update_project(project_id, status="failed")
                self.store.log_event(project_id, f"{stage} failed: {e}", "error", stage)
                self.progress(stage, f"FAILED: {e}", i / len(stages))
                return {"project_id": project_id, "status": "failed", "stage": stage, "reason": str(e)}
            self.store.set_stage(project_id, stage, "done", detail)
            self.store.log_event(project_id, detail or "done", "info", stage)
            self.progress(stage, detail or "done", (i + 1) / len(stages))
        self.store.update_project(project_id, status="completed")
        final = self.store.require_project(project_id)
        return {"project_id": project_id, "status": final["status"], "output": final["output_path"]}

    # ------------------------------------------------------------ helpers
    def _dir(self, pid: str, *parts: str) -> Path:
        d = self.store.project_dir(pid).joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _plan(self, pid: str) -> ProductionPlan:
        return ProductionPlan.model_validate(self.store.load_doc(pid, "plan"))

    def _req(self, pid: str) -> dict[str, Any]:
        return self.store.load_doc(pid, "request") or {}

    # ------------------------------------------------------------ stages
    async def _stage_plan(self, pid: str) -> str:
        r = self._req(pid)
        proj = self.store.require_project(pid)
        planner = get_planner(self.s.planning.backend, Path(r["plan_file"]) if r.get("plan_file") else None)
        plan = planner.plan(r["request"], proj["mode"], r.get("overrides"))
        if r.get("narration_script"):
            plan.audio.narration = True
        self.store.save_doc(pid, "plan", plan.model_dump(mode="json"))
        return (f"{plan.style}, {plan.duration_seconds:.0f}s, {plan.aspect_ratio}, {plan.scene_count} scenes "
                f"(planner={plan.planner})")

    async def _stage_storyboard(self, pid: str) -> str:
        existing = self.store.load_doc(pid, "storyboard")
        if existing:  # user-edited storyboard takes precedence
            sb = Storyboard.model_validate(existing)
            return f"using existing storyboard ({len(sb.scenes)} scenes)"
        sb = build_storyboard(self._plan(pid))
        self.store.save_doc(pid, "storyboard", sb.model_dump(mode="json"))
        atomic_write_text(self.store.project_dir(pid) / "storyboard.txt", sb.to_text())
        return f"{len(sb.scenes)} scenes"

    async def _stage_generate(self, pid: str) -> str:
        plan = self._plan(pid)
        sb = Storyboard.model_validate(self.store.load_doc(pid, "storyboard"))
        proj = self.store.require_project(pid)
        out_dir = self._dir(pid, "generated")
        results: dict[str, Any] = self.store.load_doc(pid, "generation") or {}
        gw, gh = _gen_size(plan.aspect_ratio)
        sem = asyncio.Semaphore(self.s.execution.generation_concurrency)
        blocked: list[str] = []
        est_total = 0.0

        async def one(scene) -> None:
            nonlocal est_total
            key = str(scene.scene_number)
            if key in results and results[key].get("path") and Path(results[key]["path"]).exists():
                return
            req = GenerationRequest(kind="video", prompt=scene.visual_prompt, negative_prompt=scene.negative_prompt,
                                    duration=scene.generation_duration, aspect_ratio=plan.aspect_ratio,
                                    width=gw, height=gh, fps=24, seed=plan.style_spec.seed + scene.scene_number,
                                    reference_image=scene.reference_image)
            decision = await self.router.route(pid, req, proj["exec_mode"])
            if decision.provider is None:
                # Fall back to generated still images (animated with camera moves) only via a real image provider.
                img_req = req.model_copy(update={"kind": "image"})
                img_decision = await self.router.route(pid, img_req, proj["exec_mode"])
                if img_decision.provider is None:
                    est_total += decision.estimated_cost
                    blocked.append(f"scene {scene.scene_number}: {decision.reason}")
                    return
                decision, req = img_decision, img_req
            if decision.cached_path:
                results[key] = {"path": decision.cached_path, "kind": req.kind, "provider": decision.provider.name,
                                "cached": True}
                return
            async with sem:
                self.progress("generate", f"scene {scene.scene_number} via {decision.reason}", 0)
                res = await self.router.run_job(pid, scene.scene_number, decision.provider, req, out_dir)
            if res.status != "succeeded" or not res.output_path:
                blocked.append(f"scene {scene.scene_number}: generation failed: {res.error}")
                return
            info = ff.probe(res.output_path) if req.kind == "video" else None
            if info is not None and (not info.has_video or info.duration <= 0):
                blocked.append(f"scene {scene.scene_number}: provider returned an invalid video file")
                return
            self.store.add_asset(pid, f"generated_{req.kind}", res.output_path, file_sha256(Path(res.output_path)),
                                 source=res.provider, license=f"AI-generated via {res.provider}; check model licence",
                                 metadata={"scene": scene.scene_number, "prompt": req.prompt, **res.metadata})
            results[key] = {"path": res.output_path, "kind": req.kind, "provider": res.provider,
                            "cost_usd": res.cost_usd, "cached": False}
            self.store.save_doc(pid, "generation", results)

        await asyncio.gather(*(one(s) for s in sb.scenes))
        self.store.save_doc(pid, "generation", results)
        if est_total:
            self.store.update_project(pid, estimated_cost_usd=est_total)
        if blocked:
            by_reason: dict[str, list[str]] = {}
            for b in blocked:
                scene, _, why = b.partition(": ")
                by_reason.setdefault(why, []).append(scene.replace("scene ", ""))
            summary = "; ".join(f"scene(s) {','.join(sorted(v, key=int))}: {k}" for k, v in by_reason.items())
            raise PipelineBlocked("Text-to-Video unavailable for this project - " + summary +
                                  ". Options: start ComfyUI with an API-format workflow (see docs/COMFYUI.md), "
                                  "enable a verified paid provider with a budget, or use video_to_video mode "
                                  "with your own footage. Completed scenes are kept for resume.")
        kinds = {v["kind"] for v in results.values()}
        return f"{len(results)} scenes generated/reused ({', '.join(sorted(kinds))})"

    async def _stage_ingest(self, pid: str) -> str:
        r = self._req(pid)
        inputs = [Path(p) for p in r.get("inputs", [])]
        files: list[Path] = []
        for p in inputs:
            if p.is_dir():
                files += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS)
            elif p.suffix.lower() in VIDEO_EXTENSIONS:
                files.append(p)
        if not files:
            raise PipelineBlocked("No input video files found (supported: " + ", ".join(sorted(VIDEO_EXTENSIONS)) + ")")
        dest_dir = self._dir(pid, "inputs")
        ingested = []
        for i, f in enumerate(files):
            if not f.exists():
                raise PipelineBlocked(f"Input file missing: {f}")
            dest = dest_dir / f"{i:03d}_{sanitize_filename(f.name)}"
            if not dest.exists():
                try:
                    os.link(f, dest)          # instant, no extra disk usage when on the same volume
                except OSError:
                    shutil.copy2(f, dest)     # originals are never modified or deleted
            try:
                info = ff.probe(dest)
            except ff.FFmpegError as e:
                self.store.log_event(pid, f"skipping unreadable input {f.name}: {e}", "warning", "ingest")
                continue
            if not info.has_video:
                continue
            self.store.add_asset(pid, "source_video", str(dest), file_sha256(dest), source="user_upload",
                                 license="user supplied", metadata=info.to_dict())
            ingested.append({"path": str(dest), "original": str(f), **info.to_dict()})
        if not ingested:
            raise PipelineBlocked("None of the input files contained a readable video stream")
        self.store.save_doc(pid, "inputs", ingested)
        return f"{len(ingested)} video file(s) ingested"

    async def _stage_analyze(self, pid: str) -> str:
        proj = self.store.require_project(pid)
        if proj["mode"] == "text_to_video":
            gen = self.store.load_doc(pid, "generation") or {}
            paths = [Path(v["path"]) for v in gen.values() if v["kind"] == "video"]
        else:
            paths = [Path(x["path"]) for x in self.store.load_doc(pid, "inputs") or []]
        cache_dir = self.s.cache_dir / "analysis"
        cache_dir.mkdir(parents=True, exist_ok=True)
        frames = self._dir(pid, "frames")
        analyses = {}

        def work(p: Path) -> tuple[str, dict]:
            cached = cache_dir / f"{file_sha256(p)}_{self.s.analysis.scene_threshold}.json"
            if cached.exists():
                import json
                d = json.loads(cached.read_text(encoding="utf-8"))
                d["path"] = str(p)
                return str(p), d
            a = analyze_clip(p, frames, self.s.analysis.weights, self.s.analysis.scene_threshold,
                             self.s.analysis.sample_frames)
            from app.utils.paths import atomic_write_json
            atomic_write_json(cached, a.to_dict())
            return str(p), a.to_dict()

        sem = asyncio.Semaphore(self.s.execution.max_workers)

        async def run(p: Path) -> None:
            async with sem:
                k, v = await asyncio.to_thread(work, p)
                analyses[k] = v
                self.progress("analyze", f"analysed {p.name} (heuristic score {v['score']:.2f})", 0)

        await asyncio.gather(*(run(p) for p in paths))
        self.store.save_doc(pid, "analysis", analyses)
        low = [Path(k).name for k, v in analyses.items() if v["score"] < 0.2]
        return f"{len(analyses)} clip(s) analysed" + (f"; low heuristic scores: {', '.join(low)}" if low else "")

    async def _stage_timeline(self, pid: str) -> str:
        plan = self._plan(pid)
        proj = self.store.require_project(pid)
        W, H = plan.width, plan.height
        out = OutputSpec(width=W, height=H, fps=plan.fps, crf=plan.export.crf, video_codec=self.s.render.video_codec,
                         preset=self.s.render.preset, audio_bitrate=self.s.render.audio_bitrate,
                         loudness_lufs=self.s.render.loudness_target_lufs, fade_in_from_black=0.4,
                         fade_out_to_black=0.8)
        notes: list[str] = []
        if proj["mode"] == "video_to_video":
            analyses = [ClipAnalysis.from_dict(v) for v in (self.store.load_doc(pid, "analysis") or {}).values()]
            analyses.sort(key=lambda a: a.path)
            music_path = self._music_path(pid, plan)
            music = analyze_music(Path(music_path)) if music_path else None
            if music:
                self.store.save_doc(pid, "music_analysis", music.to_dict())
            tl, notes = build_edit_timeline(analyses, plan.duration_seconds, plan.style, plan.aspect_ratio, music,
                                            music_path, plan.fps,
                                            output_overrides={k: v for k, v in out.model_dump().items()
                                                              if k not in ("width", "height", "fps",
                                                                           "fade_in_from_black", "fade_out_to_black")})
        else:
            sb = Storyboard.model_validate(self.store.load_doc(pid, "storyboard"))
            gen = self.store.load_doc(pid, "generation") or {}
            analysis = self.store.load_doc(pid, "analysis") or {}
            clips = []
            for sc in sb.scenes:
                g = gen[str(sc.scene_number)]
                if g["kind"] == "image":
                    clips.append(VideoClip(source=g["path"], kind="image", out_point=sc.duration,
                                           effects=Effects(zoom=0.12 if sc.scene_number % 2 else -0.12),
                                           color=plan.color_grade, scene_number=sc.scene_number,
                                           transition_in=Transition(type=sc.transition_in, duration=sc.transition_duration)))
                    notes.append(f"scene {sc.scene_number}: AI still image animated with a camera move")
                    continue
                a = analysis.get(g["path"], {})
                src_dur = a.get("info", {}).get("duration") or ff.probe(g["path"]).duration
                speed = 1.0 if src_dur >= sc.duration + 0.05 else max(0.5, (src_dur - 0.05) / sc.duration)
                length = min(src_dur - 0.05, sc.duration * speed)
                if length / speed < sc.duration - 0.01:
                    notes.append(f"scene {sc.scene_number}: generated clip too short ({src_dur:.1f}s), shortened")
                elif speed < 1:
                    notes.append(f"scene {sc.scene_number}: slowed to {speed:.2f}x to fit {sc.duration:.2f}s")
                start = max(0.0, (src_dur - length) / 2) if src_dur > length else 0.0
                clips.append(VideoClip(source=g["path"], in_point=round(start, 3), out_point=round(start + length, 3),
                                       speed=round(speed, 4), color=plan.color_grade, scene_number=sc.scene_number,
                                       effects=Effects(auto_level=a.get("mean_brightness")),
                                       transition_in=Transition(type=sc.transition_in, duration=sc.transition_duration)))
            music_path = self._music_path(pid, plan)
            tl = Timeline(clips=clips, output=out,
                          music=MusicTrack(source=music_path, volume=0.45) if music_path else None,
                          clip_audio_volume=0.3)
        self.store.save_doc(pid, "timeline", tl.model_dump(mode="json"))
        self.store.save_doc(pid, "edit_notes", notes)
        return f"{len(tl.clips)} clips, {tl.total_duration:.2f}s"

    def _music_path(self, pid: str, plan: ProductionPlan) -> str | None:
        if not plan.audio.music:
            return None
        r = self._req(pid)
        if r.get("music"):
            self.store.add_asset(pid, "music", r["music"], source="user_upload", license="user supplied")
            return r["music"]
        item = pick_music(load_library(self.s.assets_dir / "music"), plan.audio.music_style)
        if item is None:
            self.store.log_event(pid, "music requested but assets/music is empty - rendering without music",
                                 "warning", "timeline")
            return None
        self.store.add_asset(pid, "music", str(item.path), source="local_library", license=item.license)
        return str(item.path)

    async def _stage_audio(self, pid: str) -> str:
        plan = self._plan(pid)
        tl = Timeline.model_validate(self.store.load_doc(pid, "timeline"))
        proj = self.store.require_project(pid)
        msgs = []
        starts = tl.clip_start_times()
        lines: list[tuple[int, str]] = []
        script = self._req(pid).get("narration_script")
        if proj["mode"] == "text_to_video":
            sb = Storyboard.model_validate(self.store.load_doc(pid, "storyboard"))
            scene_start = {c.scene_number: st for c, st in zip(tl.clips, starts) if c.scene_number}
            if plan.audio.narration:
                lines = [(s.scene_number, s.narration) for s in sb.scenes if s.narration]
            if plan.audio.sound_effects:
                lib = load_library(self.s.assets_dir / "sfx")
                for s in sb.scenes:
                    for desc in s.sound_effects:
                        if it := match(lib, desc):
                            tl.sfx.append(AudioCue(source=str(it.path), start=scene_start.get(s.scene_number, 0),
                                                   volume=0.6, label=desc, license=it.license))
                if not lib:
                    msgs.append("sound effects requested but assets/sfx is empty")
        else:
            scene_start = {i + 1: st for i, st in enumerate(starts)}
        if script:
            # User script: one sentence per clip slot, in order.
            import re
            sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", script.strip()) if x.strip()]
            lines = [(min(i + 1, len(starts)), t) for i, t in enumerate(sentences)]
            if proj["mode"] == "text_to_video":
                lines = [(tl.clips[min(i, len(tl.clips) - 1)].scene_number or 1, t) for i, t in enumerate(sentences)]
        if lines:
            backend = get_tts_backend()
            if backend is None:
                msgs.append("narration requested but no offline TTS engine installed (pip install pyttsx3 on "
                            "Windows, or install espeak-ng/piper) - rendered without narration")
                self.store.log_event(pid, msgs[-1], "warning", "audio")
            else:
                synth = await asyncio.to_thread(synthesize_lines, lines, self._dir(pid, "narration"), backend,
                                                plan.audio.voice, plan.audio.speech_rate)
                cues, warns = narration_cues(scene_start, synth)
                tl.narration = cues
                msgs += warns
                for c in cues:
                    self.store.add_asset(pid, "narration", c.source, source=f"tts:{backend.name}", license=c.license)
                if plan.audio.subtitles:
                    text_by_scene = dict(lines)
                    placements = [(c.start, d, text_by_scene[sn]) for c, (sn, _w, d) in zip(cues, synth)]
                    cues_ = cues_from_narration(placements)
                    sub_dir = self._dir(pid, "subtitles")
                    write_srt(cues_, sub_dir / "subtitles.srt")
                    style = "social_media" if plan.aspect_ratio == "9:16" else "minimal_documentary"
                    ass = write_ass(cues_, sub_dir / "subtitles.ass", plan.width, plan.height, style)
                    if "subtitles" in ff.list_filters():
                        tl.subtitles = SubtitleTrack(path=str(ass), burn_in=True)
                    else:
                        msgs.append("FFmpeg lacks libass: SRT written but subtitles not burned in")
                msgs.append(f"{len(cues)} narration line(s) via {backend.name}")
        if tl.music:
            msgs.append(f"music: {Path(tl.music.source).name}")
        self.store.save_doc(pid, "timeline", Timeline.model_validate(tl.model_dump()).model_dump(mode="json"))
        return "; ".join(msgs) or "clip audio only"

    async def _stage_render(self, pid: str) -> str:
        tl = Timeline.model_validate(self.store.load_doc(pid, "timeline"))
        free_gb = shutil.disk_usage(self.store.project_dir(pid)).free / 1024**3
        if free_gb < self.s.execution.min_free_disk_gb:
            raise PipelineBlocked(f"Only {free_gb:.1f} GB free disk; need {self.s.execution.min_free_disk_gb} GB")
        out = self._dir(pid, "output") / f"{self.store.require_project(pid)['name']}.mp4"
        work = self._dir(pid, "tmp")

        def prog(f: float) -> None:
            self.progress("render", f"{f * 100:.0f}%", f)

        plan = await asyncio.to_thread(render, tl, out, work, prog)
        self.store.save_doc(pid, "render", {"encoder": plan.encoder, "duration": plan.total_duration,
                                            "notes": plan.notes, "ffmpeg_args": plan.args})
        self.store.update_project(pid, output_path=str(out))
        self.store.add_asset(pid, "render", str(out), source="ffmpeg", license="project output")
        return f"{out.name} ({plan.encoder}, {plan.total_duration:.2f}s)"

    async def _stage_validate(self, pid: str) -> str:
        tl = Timeline.model_validate(self.store.load_doc(pid, "timeline"))
        out = Path(self.store.require_project(pid)["output_path"])
        black_allow = 0.5 + sum(c.transition_in.duration for c in tl.clips if c.transition_in.type == "fade_black")
        exp = Expectations(duration=tl.total_duration, width=tl.output.width, height=tl.output.height,
                           fps=tl.output.fps, allowed_black_head=tl.output.fade_in_from_black,
                           allowed_black_tail=tl.output.fade_out_to_black, max_black_seconds=black_allow)
        rep = validate_file(out, exp)
        attempts = 0
        render_info = self.store.load_doc(pid, "render") or {}
        # Bounded, safe repair: one re-render with software x264 if a hardware encoder produced a bad file.
        while not rep.passed and attempts < min(1, self.s.execution.max_retries) and \
                render_info.get("encoder") not in (None, "libx264"):
            attempts += 1
            self.store.log_event(pid, "QC failed - re-rendering with libx264", "warning", "validate")
            await asyncio.to_thread(render, tl, out, self._dir(pid, "tmp"), None, "libx264")
            rep = validate_file(out, exp)
        self.store.save_doc(pid, "qc_report", rep.to_dict())
        atomic_write_text(self.store.project_dir(pid) / "qc_report.txt", rep.summary())
        if not rep.passed:
            raise RuntimeError("QC failed: " + "; ".join(f"{c.name} (expected {c.expected}, got {c.actual})"
                                                        for c in rep.errors))
        shutil.rmtree(self.store.project_dir(pid) / "tmp", ignore_errors=True)  # only after validated success
        return f"QC passed ({len(rep.warnings)} warning(s))"

    async def _stage_report(self, pid: str) -> str:
        proj = self.store.require_project(pid)
        plan = self._plan(pid)
        qc = self.store.load_doc(pid, "qc_report") or {}
        jobs = self.store.jobs(pid)
        notes = self.store.load_doc(pid, "edit_notes") or []
        assets = self.store.assets(pid)
        report = {
            "project_id": pid, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": proj["user_request"], "mode": proj["mode"], "execution_mode": proj["exec_mode"],
            "output": proj["output_path"], "plan_summary": {
                "style": plan.style, "duration": plan.duration_seconds, "aspect_ratio": plan.aspect_ratio,
                "resolution": plan.resolution, "scenes": plan.scene_count, "planner": plan.planner},
            "stages": self.store.stages(pid), "generation_jobs": jobs,
            "costs": {"estimated_usd": proj["estimated_cost_usd"], "actual_usd": proj["actual_cost_usd"]},
            "editing_decisions": notes, "render": self.store.load_doc(pid, "render"),
            "qc": {"passed": qc.get("passed"), "errors": qc.get("errors"), "warnings": qc.get("warnings")},
            "assets_and_licences": [{"kind": a["kind"], "path": a["path"], "source": a["source"],
                                     "license": a["license"]} for a in assets],
            "limitations": [qc.get("creative_notes", ""),
                            "Clip scores are technical heuristics, not artistic judgement.",
                            "No semantic visual understanding was performed (no vision model configured)."],
        }
        self.store.save_doc(pid, "production_report", report)
        md = [f"# Production report - {proj['name']}", "", f"**Request:** {proj['user_request']}", "",
              f"- Mode: {proj['mode']} ({proj['exec_mode']})", f"- Output: `{proj['output_path']}`",
              f"- Plan: {plan.style}, {plan.duration_seconds:.0f}s, {plan.aspect_ratio}, {plan.scene_count} scenes",
              f"- Cost: estimated ${proj['estimated_cost_usd']:.2f}, actual ${proj['actual_cost_usd']:.2f}",
              f"- QC: {'PASSED' if qc.get('passed') else 'FAILED'} ({qc.get('warnings', 0)} warnings)", "",
              "## Editing decisions", *[f"- {n}" for n in notes], "", "## Assets & licences",
              *[f"- {a['kind']}: {Path(a['path']).name} - {a['source']} - {a['license']}" for a in assets], "",
              "## Limitations", *[f"- {x}" for x in report["limitations"]]]
        atomic_write_text(self.store.project_dir(pid) / "production_report.md", "\n".join(md) + "\n")
        self.store.update_project(pid, status="completed")
        return "production_report.md written"


def _gen_size(aspect: str) -> tuple[int, int]:
    """Generation resolution (smaller than delivery; the renderer upscales)."""
    return {"9:16": (480, 848), "16:9": (848, 480), "1:1": (640, 640), "4:5": (576, 720)}.get(aspect, (848, 480))
