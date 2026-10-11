"""Local Gradio dashboard wired to the real pipeline. Binds to 127.0.0.1 by default."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any, Iterator

import gradio as gr

from app.config import get_settings
from app.database.storage import ProjectStore
from app.pipeline.orchestrator import Orchestrator

STYLES = ["auto", "cinematic", "product_commercial", "technology_showcase", "fast_paced", "documentary",
          "relaxed_storytelling"]


def _store() -> tuple[Any, ProjectStore]:
    s = get_settings()
    s.ensure_dirs()
    return s, ProjectStore(s.database_path, s.projects_dir)


def _outputs(store: ProjectStore, pid: str) -> tuple[str | None, str, str, str]:
    d = store.project_dir(pid)
    p = store.get_project(pid) or {}
    video = p.get("output_path") if p.get("output_path") and Path(p["output_path"]).exists() else None
    read = lambda name: (d / name).read_text(encoding="utf-8") if (d / name).exists() else ""  # noqa: E731
    return video, read("production_report.md"), read("storyboard.txt"), read("qc_report.txt")


def _run_streaming(pid: str, from_stage: str | None = None) -> Iterator[tuple]:
    s, store = _store()
    q: queue.Queue = queue.Queue()
    log_lines: list[str] = []

    def progress(stage: str, msg: str, frac: float) -> None:
        q.put((stage, msg, frac))

    result: dict[str, Any] = {}
    t = threading.Thread(target=lambda: result.update(Orchestrator(s, store, progress=progress).run(pid, from_stage)),
                         daemon=True)
    t.start()
    last_render = ""
    while t.is_alive() or not q.empty():
        try:
            stage, msg, frac = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if stage == "render" and msg.endswith("%"):
            last_render = f"render {msg}"
        else:
            log_lines.append(f"[{stage}] {msg}")
        status = f"**Project** `{pid}` - stage **{stage}** - {frac * 100:.0f}%" + (f" - {last_render}" if last_render else "")
        yield status, "\n".join(log_lines[-40:]), None, "", "", ""
    t.join()
    video, report, sb, qc = _outputs(store, pid)
    st = result.get("status", "unknown")
    headline = f"**{st.upper()}** - `{pid}`"
    if st != "completed":
        headline += f"\n\n**Stage:** {result.get('stage')}\n\n**Reason:** {result.get('reason')}"
    yield headline, "\n".join(log_lines), video, report, sb, qc


def create_and_run(prompt: str, mode: str, files: list | None, music: str | None, aspect: str, duration: float,
                   style: str, exec_mode: str, budget: float, script: str) -> Iterator[tuple]:
    if not prompt.strip():
        yield "Enter a prompt first.", "", None, "", "", ""
        return
    s, store = _store()
    inputs = [Path(f if isinstance(f, str) else f.name) for f in (files or [])]
    if mode == "video_to_video" and not inputs:
        yield "Upload at least one video for Existing Video mode.", "", None, "", "", ""
        return
    overrides: dict[str, Any] = {"aspect_ratio": aspect}
    if duration:
        overrides["duration_seconds"] = float(duration)
    if style != "auto":
        overrides["style"] = style
    pid = Orchestrator(s, store).create_project(prompt, mode, inputs, overrides, None, exec_mode, float(budget or 0),
                                                Path(music) if music else None, script.strip() or None)
    yield from _run_streaming(pid)


def list_projects() -> list[list[Any]]:
    _s, store = _store()
    return [[p["id"], p["mode"], p["status"], p["current_stage"], f"${p['actual_cost_usd']:.2f}", p["created_at"]]
            for p in store.list_projects()]


def project_ids() -> gr.Dropdown:
    return gr.Dropdown(choices=[r[0] for r in list_projects()])


def view_project(pid: str) -> tuple:
    if not pid:
        return None, "", "", "", ""
    _s, store = _store()
    stages = "\n".join(f"- **{k}**: {v['status']} {v['detail'] or ''}" for k, v in store.stages(pid).items())
    return (*_outputs(store, pid), stages)


def resume(pid: str) -> Iterator[tuple]:
    if not pid:
        yield "Select a project.", "", None, "", "", ""
        return
    yield from _run_streaming(pid)


def doctor_text() -> str:
    from app.system.doctor import run_doctor
    r = run_doctor(get_settings())
    gpu = "; ".join(f"{g.name} ({g.vram_total_mb} MB)" for g in r.gpus) or "none detected"
    lines = [f"**OS:** {r.os}", f"**Python:** {r.python}", f"**FFmpeg:** {r.ffmpeg_version}",
             f"**Encoders:** {', '.join(r.h264_encoders_working)}", f"**GPU:** {gpu}",
             f"**RAM:** {r.ram_total_gb} GB  **Disk free:** {r.disk_free_gb} GB",
             f"**ComfyUI:** {'reachable' if r.comfyui['reachable'] else 'offline'}",
             f"**Tier:** {r.hardware_tier}", "", "### Features"]
    lines += [f"- **{k}**: {v}" for k, v in r.features.items()]
    lines += ["", "### Recommendations"] + [f"- {x}" for x in r.recommendations]
    return "\n".join(lines)


def build_ui() -> gr.Blocks:
    s = get_settings()
    with gr.Blocks(title="AutoCine AI Studio") as ui:
        gr.Markdown("# AutoCine AI Studio\nNatural-language video production - local-first, paid APIs off by default.")
        with gr.Tab("Create"):
            with gr.Row():
                with gr.Column(scale=3):
                    prompt = gr.Textbox(label="Video request", lines=4,
                                        placeholder="Create a 30-second cinematic commercial showing ...")
                    mode = gr.Radio(["text_to_video", "video_to_video"], value="video_to_video",
                                    label="Production mode (text_to_video needs a configured generator)")
                    files = gr.File(label="Existing videos (video_to_video)", file_count="multiple",
                                    file_types=["video"], type="filepath")
                    music = gr.Audio(label="Music (only tracks you are licensed to use)", type="filepath")
                    script = gr.Textbox(label="Narration script (optional, one sentence per shot)", lines=3)
                with gr.Column(scale=2):
                    aspect = gr.Dropdown(["9:16", "16:9", "1:1", "4:5"], value="9:16", label="Aspect ratio")
                    duration = gr.Number(value=30, label="Duration (seconds)", minimum=2, maximum=600)
                    style = gr.Dropdown(STYLES, value="auto", label="Visual style")
                    exec_mode = gr.Radio(["hybrid", "local", "cloud"], value=s.execution.mode, label="AI mode")
                    budget = gr.Number(value=s.budget.max_project_budget_usd, label="Max budget (USD)", minimum=0)
                    gr.Markdown(f"Paid generation globally **{'ENABLED' if s.budget.allow_paid_generation else 'DISABLED'}**"
                                " (ALLOW_PAID_GENERATION). Per-project approval is still required.")
                    go = gr.Button("Generate", variant="primary")
            status = gr.Markdown()
            log = gr.Textbox(label="Production monitor", lines=12)
            with gr.Row():
                video = gr.Video(label="Final video")
                report = gr.Markdown(label="Production report")
            with gr.Accordion("Storyboard / QC", open=False):
                sb = gr.Textbox(label="Storyboard", lines=12)
                qc = gr.Textbox(label="Quality-control report", lines=12)
            go.click(create_and_run, [prompt, mode, files, music, aspect, duration, style, exec_mode, budget, script],
                     [status, log, video, report, sb, qc])
        with gr.Tab("Projects"):
            table = gr.Dataframe(headers=["id", "mode", "status", "stage", "cost", "created"], value=list_projects,
                                 interactive=False)
            with gr.Row():
                pid = gr.Dropdown(choices=[r[0] for r in list_projects()], label="Project")
                refresh = gr.Button("Refresh")
                resume_btn = gr.Button("Resume", variant="primary")
            stages_md = gr.Markdown()
            p_status = gr.Markdown()
            p_log = gr.Textbox(label="Monitor", lines=8)
            with gr.Row():
                p_video = gr.Video(label="Output")
                p_report = gr.Markdown()
            p_sb = gr.Textbox(label="Storyboard", lines=8)
            p_qc = gr.Textbox(label="QC report", lines=8)
            refresh.click(list_projects, None, table).then(project_ids, None, pid)
            pid.change(view_project, pid, [p_video, p_report, p_sb, p_qc, stages_md])
            resume_btn.click(resume, pid, [p_status, p_log, p_video, p_report, p_sb, p_qc])
        with gr.Tab("System"):
            doc = gr.Markdown()
            gr.Button("Run diagnostics").click(doctor_text, None, doc)
    return ui


def launch(share: bool = False, port: int | None = None) -> None:
    s = get_settings()
    build_ui().queue().launch(server_name=s.ui.host, server_port=port or s.ui.port, share=share,
                              allowed_paths=[str(s.projects_dir)])
