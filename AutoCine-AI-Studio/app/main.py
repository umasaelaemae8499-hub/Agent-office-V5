"""AutoCine AI Studio command-line interface.

  python -m app.main doctor
  python -m app.main create --prompt "..." [--input FOLDER_OR_FILES ...]
  python -m app.main plan --prompt "..."            (preview plan + storyboard, no rendering)
  python -m app.main resume --project ID [--from-stage timeline]
  python -m app.main render --project ID            (re-render after editing timeline.json)
  python -m app.main validate --project ID
  python -m app.main approve --project ID --budget 5
  python -m app.main list | status --project ID
  python -m app.main ui
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app import __version__
from app.config import get_settings
from app.logging_setup import setup_logging


def _store():
    from app.database.storage import ProjectStore
    s = get_settings()
    s.ensure_dirs()
    return s, ProjectStore(s.database_path, s.projects_dir)


def _printer(stage: str, msg: str, frac: float) -> None:
    if stage == "render" and msg.endswith("%"):
        print(f"\r  [render] {msg}   ", end="", flush=True)
        if msg == "100%":
            print()
        return
    print(f"  [{stage:<10}] {msg}")


def _overrides(a: argparse.Namespace) -> dict:
    o = {}
    if a.duration:
        o["duration_seconds"] = a.duration
    if a.aspect:
        o["aspect_ratio"] = a.aspect
    if a.style:
        o["style"] = a.style
    if getattr(a, "name", None):
        o["project_name"] = a.name
    if getattr(a, "reference", None):
        o["reference_images"] = [str(Path(r).resolve()) for r in a.reference]
    return o


def cmd_doctor(a: argparse.Namespace) -> int:
    from app.system.doctor import run_doctor
    s = get_settings()
    r = run_doctor(s)
    if a.json:
        print(json.dumps(r.to_dict(), indent=2, default=str))
        return 0
    print(f"AutoCine AI Studio {__version__} - environment diagnostics\n")
    print(f"OS              : {r.os}")
    print(f"Python          : {r.python} {'OK' if r.python_ok else '(need 3.11+)'}")
    print(f"FFmpeg          : {r.ffmpeg_version or 'NOT FOUND'}")
    print(f"FFprobe         : {r.ffprobe or 'NOT FOUND'}")
    print(f"Filters missing : {', '.join(r.required_filters_missing) or 'none'}")
    print(f"H.264 encoders  : {', '.join(r.h264_encoders_working) or 'none'} (selected: {r.selected_encoder})")
    print(f"CPU cores       : {r.cpu_count}")
    print(f"RAM             : {r.ram_total_gb} GB")
    print(f"Disk free       : {r.disk_free_gb} GB (projects drive)")
    if r.gpus:
        for g in r.gpus:
            print(f"GPU             : {g.name} | VRAM {g.vram_total_mb} MB | driver {g.driver}")
    else:
        print("GPU             : none detected (no nvidia-smi / video controller info)")
    print(f"CUDA (torch)    : {'not installed' if r.cuda_available is None else r.cuda_available}")
    print(f"ComfyUI         : {'reachable' if r.comfyui['reachable'] else 'not reachable'} at {r.comfyui['url']}"
          f"{' | install: ' + r.comfyui['install_dir'] if r.comfyui['install_dir'] else ''}")
    print(f"TTS engines     : {', '.join(r.tts_engines) or 'none'}")
    print(f"Cloud API keys  : {', '.join(f'{k}={v}' for k, v in r.cloud_credentials.items()) or 'none'}")
    print(f"Python packages : " + ", ".join(f"{k}{'' if v else ' (missing)'}" for k, v in r.python_packages.items()))
    print(f"Hardware tier   : {r.hardware_tier}\n")
    print("Feature availability:")
    for k, v in r.features.items():
        print(f"  - {k:<32} {v}")
    print("\nRecommendations:")
    for rec in r.recommendations:
        print(f"  * {rec}")
    return 0


def cmd_create(a: argparse.Namespace) -> int:
    from app.pipeline.orchestrator import Orchestrator
    s, store = _store()
    mode = a.mode or ("video_to_video" if a.input else "text_to_video")
    if mode == "video_to_video" and not a.input:
        print("video_to_video mode needs --input FOLDER_OR_FILES", file=sys.stderr)
        return 2
    orch = Orchestrator(s, store, progress=_printer)
    script = Path(a.narration_script).read_text(encoding="utf-8") if a.narration_script else None
    pid = orch.create_project(a.prompt, mode, [Path(p) for p in a.input or []], _overrides(a),
                              Path(a.plan_file) if a.plan_file else None, a.exec_mode, a.budget,
                              Path(a.music) if a.music else None, script)
    print(f"Project: {pid}")
    if a.no_run:
        return 0
    return _report(orch.run(pid), store)


def _report(result: dict, store) -> int:
    st = result["status"]
    print(f"\nStatus: {st.upper()}")
    if st == "completed":
        d = store.project_dir(result["project_id"])
        print(f"Video : {result.get('output')}\nReport: {d / 'production_report.md'}")
        return 0
    print(f"Stage : {result.get('stage')}\nReason: {result.get('reason')}")
    print(f"Resume: python -m app.main resume --project {result['project_id']}")
    return 3 if st == "blocked" else 1


def cmd_plan(a: argparse.Namespace) -> int:
    from app.agents.director import get_planner
    from app.agents.storyboard import build_storyboard
    s = get_settings()
    plan = get_planner(s.planning.backend, Path(a.plan_file) if a.plan_file else None).plan(
        a.prompt, a.mode or "text_to_video", _overrides(a))
    sb = build_storyboard(plan)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (out / "storyboard.json").write_text(sb.model_dump_json(indent=2), encoding="utf-8")
        print(f"Wrote {out / 'plan.json'} and {out / 'storyboard.json'}")
    else:
        print(plan.model_dump_json(indent=2))
    print("\n" + sb.to_text())
    return 0


def cmd_resume(a: argparse.Namespace) -> int:
    from app.pipeline.orchestrator import Orchestrator
    s, store = _store()
    store.require_project(a.project)
    return _report(Orchestrator(s, store, progress=_printer).run(a.project, a.from_stage), store)


def cmd_render(a: argparse.Namespace) -> int:
    a.from_stage = "render"
    return cmd_resume(a)


def cmd_validate(a: argparse.Namespace) -> int:
    from app.quality.validator import Expectations, validate_file
    from app.video.timeline import Timeline
    s, store = _store()
    p = store.require_project(a.project)
    if not p["output_path"]:
        print("Project has no rendered output yet.", file=sys.stderr)
        return 2
    tl = Timeline.model_validate(store.load_doc(a.project, "timeline"))
    rep = validate_file(Path(p["output_path"]), Expectations(
        duration=tl.total_duration, width=tl.output.width, height=tl.output.height, fps=tl.output.fps,
        allowed_black_head=tl.output.fade_in_from_black, allowed_black_tail=tl.output.fade_out_to_black,
        max_black_seconds=0.5 + sum(c.transition_in.duration for c in tl.clips if c.transition_in.type == "fade_black")))
    print(rep.summary())
    store.save_doc(a.project, "qc_report", rep.to_dict())
    return 0 if rep.passed else 1


def cmd_approve(a: argparse.Namespace) -> int:
    s, store = _store()
    store.require_project(a.project)
    if not s.budget.allow_paid_generation:
        print("ALLOW_PAID_GENERATION is false in .env/settings - set it to true first. Nothing approved.")
        return 2
    store.update_project(a.project, paid_approved=1, budget_usd=a.budget)
    store.log_event(a.project, f"paid generation approved up to ${a.budget:.2f}", "warning")
    print(f"Approved paid generation for {a.project} up to ${a.budget:.2f} "
          f"(global cap ${s.budget.max_project_budget_usd:.2f}).")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    _s, store = _store()
    rows = store.list_projects()
    if not rows:
        print("No projects yet.")
    for p in rows:
        print(f"{p['id']:<48} {p['mode']:<15} {p['status']:<10} stage={p['current_stage'] or '-':<10} {p['created_at']}")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    _s, store = _store()
    p = store.require_project(a.project)
    print(json.dumps(p, indent=2))
    for name, st in store.stages(a.project).items():
        print(f"  {name:<11} {st['status']:<8} {st['detail'] or ''}")
    return 0


def cmd_ui(a: argparse.Namespace) -> int:
    from app.ui.dashboard import launch
    launch(share=False, port=a.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.main", description="AutoCine AI Studio")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="inspect hardware/software and report available features")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_doctor)

    def creative(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--prompt", required=True, help="natural-language brief")
        sp.add_argument("--mode", choices=["text_to_video", "video_to_video"])
        sp.add_argument("--duration", type=float, help="seconds (overrides the brief)")
        sp.add_argument("--aspect", choices=["9:16", "16:9", "1:1", "4:5"])
        sp.add_argument("--style", choices=["cinematic", "product_commercial", "technology_showcase",
                                            "fast_paced", "documentary", "relaxed_storytelling"])
        sp.add_argument("--plan-file", help="use a hand/Claude-authored plan.json instead of the planner")

    c = sub.add_parser("create", help="create a project and run the full pipeline")
    creative(c)
    c.add_argument("--input", nargs="+", help="video files or folders (video_to_video mode)")
    c.add_argument("--music", help="music file you are licensed to use")
    c.add_argument("--narration-script", help="text file with narration (one sentence per shot)")
    c.add_argument("--reference", nargs="+", help="reference image(s) for consistent subjects")
    c.add_argument("--name", help="project name")
    c.add_argument("--exec-mode", choices=["local", "cloud", "hybrid"])
    c.add_argument("--budget", type=float, help="project budget in USD (default from settings, 0)")
    c.add_argument("--no-run", action="store_true", help="create only; run later with resume")
    c.set_defaults(fn=cmd_create)

    pl = sub.add_parser("plan", help="preview the production plan + storyboard without producing")
    creative(pl)
    pl.add_argument("--out", help="folder to write plan.json + storyboard.json")
    pl.set_defaults(fn=cmd_plan)

    for name, fn, hlp in (("resume", cmd_resume, "continue an interrupted/blocked project"),
                          ("render", cmd_render, "re-render (e.g. after editing timeline.json)"),
                          ("validate", cmd_validate, "run QC on the rendered output"),
                          ("status", cmd_status, "show project state")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--project", required=True)
        if name == "resume":
            sp.add_argument("--from-stage", choices=["plan", "storyboard", "ingest", "generate", "analyze",
                                                     "timeline", "audio", "render", "validate", "report"])
        sp.set_defaults(fn=fn)

    ap = sub.add_parser("approve", help="authorise paid generation for one project")
    ap.add_argument("--project", required=True)
    ap.add_argument("--budget", type=float, required=True)
    ap.set_defaults(fn=cmd_approve)

    sub.add_parser("list", help="list projects").set_defaults(fn=cmd_list)
    u = sub.add_parser("ui", help="launch the local web dashboard")
    u.add_argument("--port", type=int)
    u.set_defaults(fn=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s = get_settings()
    setup_logging(s.logs_dir, logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return args.fn(args)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
