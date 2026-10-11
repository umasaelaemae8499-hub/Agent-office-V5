"""Compile a Timeline into one deterministic FFmpeg invocation and run it.

Everything (trim, speed, fit, effects, grade, transitions, audio mix, ducking,
loudness, subtitles) happens in a single filter graph, so sources are decoded
once and encoded once - no lossy intermediate re-encodes.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.utils import ffmpeg as ff
from app.video.color import auto_level_filter, color_filter
from app.video.timeline import Timeline, VideoClip
from app.video.transitions import xfade_name

log = logging.getLogger(__name__)


@dataclass
class RenderPlan:
    args: list[str]
    filter_graph: str
    total_duration: float
    encoder: str
    notes: list[str] = field(default_factory=list)


def _atempo_chain(speed: float) -> str:
    """atempo accepts 0.5..2.0 per instance; chain for larger factors."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def _fit_filters(clip: VideoClip, src_w: int, src_h: int, W: int, H: int, label_in: str,
                 label_out: str, idx: int) -> list[str]:
    fit = clip.fit
    if fit == "auto":
        if src_w and src_h:
            ratio = (src_w / src_h) / (W / H)
            fit = "cover" if 0.8 <= ratio <= 1.25 else "blur_pad"
        else:
            fit = "cover"
    if fit == "cover":
        return [f"[{label_in}]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},setsar=1[{label_out}]"]
    if fit == "contain":
        return [f"[{label_in}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{label_out}]"]
    # blur_pad: blurred cover-scaled background + contained foreground
    return [
        f"[{label_in}]split=2[bg{idx}][fg{idx}]",
        f"[bg{idx}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,"
        f"eq=brightness=-0.08[bgb{idx}]",
        f"[fg{idx}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fgs{idx}]",
        f"[bgb{idx}][fgs{idx}]overlay=(W-w)/2:(H-h)/2,setsar=1[{label_out}]",
    ]


def _effect_filters(clip: VideoClip, W: int, H: int) -> list[str]:
    fx, D = clip.effects, max(clip.duration, 0.01)
    out: list[str] = []
    if fx.zoom or fx.pan != "none":
        z = abs(fx.zoom) or 0.08  # panning needs some headroom
        # zoom in: scale grows 1 -> 1+z ; zoom out: 1+z -> 1
        grow = f"(1+{z}*t/{D:.4f})" if fx.zoom >= 0 else f"(1+{z}*(1-t/{D:.4f}))"
        out.append(f"scale=w='trunc({W}*{grow}/2)*2':h='trunc({H}*{grow}/2)*2':eval=frame:flags=bicubic")
        prog = f"min(1,t/{D:.4f})"
        x = {"left": f"(iw-{W})*(1-{prog})", "right": f"(iw-{W})*{prog}"}.get(fx.pan, f"(iw-{W})/2")
        y = {"up": f"(ih-{H})*(1-{prog})", "down": f"(ih-{H})*{prog}"}.get(fx.pan, f"(ih-{H})/2")
        out.append(f"crop={W}:{H}:x='{x}':y='{y}'")
    if fx.auto_level is not None:
        if f := auto_level_filter(fx.auto_level):
            out.append(f)
    if c := color_filter(clip.color):
        out.append(c)
    if fx.blur:
        out.append(f"gblur=sigma={fx.blur}")
    if fx.sharpen:
        out.append(f"unsharp=5:5:{fx.sharpen}")
    if fx.vignette:
        out.append("vignette=PI/5")
    return out


def build_render_plan(tl: Timeline, output: Path, encoder: str | None = None,
                      subtitle_filename: str | None = None) -> RenderPlan:
    o = tl.output
    W, H, fps = o.width, o.height, o.fps
    enc = encoder or ff.select_video_encoder(o.video_codec)
    args: list[str] = ["-y"]
    graph: list[str] = []
    notes: list[str] = []
    n_inputs = 0

    def add_input(*a: str) -> int:
        nonlocal n_inputs
        args.extend(a)
        n_inputs += 1
        return n_inputs - 1

    # ---------- per-clip video + audio ----------
    clip_v, clip_a = [], []
    for i, clip in enumerate(tl.clips):
        src = str(Path(clip.source).resolve())
        info = ff.probe(src) if clip.kind == "video" else None
        if clip.kind == "image":
            idx = add_input("-loop", "1", "-framerate", str(fps), "-t", f"{clip.duration:.4f}", "-i", src)
            sw = sh = 0
            try:
                from PIL import Image
                with Image.open(src) as im:
                    sw, sh = im.size
            except Exception:
                pass
        else:
            assert info is not None
            if clip.out_point > info.duration + 0.05:
                raise ValueError(f"Clip {src} out_point {clip.out_point:.2f}s exceeds source duration "
                                 f"{info.duration:.2f}s")
            idx = add_input("-ss", f"{clip.in_point:.4f}", "-t", f"{clip.out_point - clip.in_point:.4f}",
                            "-i", src)
            sw, sh = info.width, info.height
        chain_in = f"{idx}:v"
        pre = f"[{chain_in}]setpts=(PTS-STARTPTS)/{clip.speed:.6f},fps={fps},format=yuv420p[pre{i}]"
        graph.append(pre)
        graph += _fit_filters(clip, sw, sh, W, H, f"pre{i}", f"fit{i}", i)
        fx = _effect_filters(clip, W, H)
        # trim guarantees exact duration (needed for xfade offset maths)
        tail = ",".join([*fx, f"trim=duration={clip.duration:.4f}", "setpts=PTS-STARTPTS", "settb=AVTB"])
        graph.append(f"[fit{i}]{tail}[v{i}]")
        clip_v.append(f"v{i}")

        has_audio = bool(info and info.has_audio) and clip.use_audio
        if has_audio:
            achain = [f"asetpts=PTS-STARTPTS"]
            if clip.speed != 1.0:
                achain.append(_atempo_chain(clip.speed))
            achain += [f"aresample={o.sample_rate}", "aformat=sample_fmts=fltp:channel_layouts=stereo",
                       f"apad", f"atrim=duration={clip.duration:.4f}"]
            graph.append(f"[{idx}:a]{','.join(achain)}[a{i}]")
        else:
            graph.append(f"anullsrc=r={o.sample_rate}:cl=stereo,atrim=duration={clip.duration:.4f}[a{i}]")
        clip_a.append(f"a{i}")

    # ---------- join clips: xfade/acrossfade for transitions, concat for cuts ----------
    acc_v, acc_a, acc_d = clip_v[0], clip_a[0], tl.clips[0].duration
    for i in range(1, len(tl.clips)):
        clip, tr = tl.clips[i], tl.clips[i].transition_in
        nv, na = f"jv{i}", f"ja{i}"
        if tr.type == "cut":
            graph.append(f"[{acc_v}][{acc_a}][{clip_v[i]}][{clip_a[i]}]concat=n=2:v=1:a=1[{nv}][{na}]")
            acc_d += clip.duration
        else:
            off = acc_d - tr.duration
            graph.append(f"[{acc_v}][{clip_v[i]}]xfade=transition={xfade_name(tr.type)}:"
                         f"duration={tr.duration:.4f}:offset={off:.4f}[{nv}]")
            graph.append(f"[{acc_a}][{clip_a[i]}]acrossfade=d={tr.duration:.4f}:c1=tri:c2=tri[{na}]")
            acc_d = acc_d + clip.duration - tr.duration
        acc_v, acc_a = nv, na
    total = acc_d

    # ---------- global video finishing ----------
    vpost = []
    if o.fade_in_from_black:
        vpost.append(f"fade=t=in:st=0:d={o.fade_in_from_black:.3f}")
    if o.fade_out_to_black:
        vpost.append(f"fade=t=out:st={max(0, total - o.fade_out_to_black):.3f}:d={o.fade_out_to_black:.3f}")
    if tl.subtitles and tl.subtitles.burn_in and subtitle_filename:
        vpost.append(f"subtitles={subtitle_filename}")
    vpost.append("format=yuv420p")
    graph.append(f"[{acc_v}]{','.join(vpost)}[vout]")

    # ---------- audio mix ----------
    mix = [f"[{acc_a}]volume={tl.clip_audio_volume:.3f}[bed]"]
    mix_inputs = ["[bed]"]
    narr_labels = []
    for k, cue in enumerate(tl.narration):
        idx = add_input("-i", str(Path(cue.source).resolve()))
        ms = int(cue.start * 1000)
        mix.append(f"[{idx}:a]aresample={o.sample_rate},aformat=channel_layouts=stereo,"
                   f"volume={cue.volume:.3f},adelay={ms}|{ms}[nar{k}]")
        narr_labels.append(f"[nar{k}]")
    narr_bus = None
    if narr_labels:
        if len(narr_labels) == 1:
            narr_bus = narr_labels[0]
        else:
            mix.append(f"{''.join(narr_labels)}amix=inputs={len(narr_labels)}:normalize=0:duration=longest[narbus]")
            narr_bus = "[narbus]"
    if tl.music:
        m = tl.music
        loop = ["-stream_loop", "-1"] if m.loop else []
        idx = add_input(*loop, "-ss", f"{m.offset:.3f}", "-i", str(Path(m.source).resolve()))
        mf = [f"aresample={o.sample_rate}", "aformat=channel_layouts=stereo",
              f"atrim=duration={total:.4f}", "asetpts=PTS-STARTPTS", f"volume={m.volume:.3f}"]
        if m.fade_in:
            mf.append(f"afade=t=in:st=0:d={m.fade_in:.3f}")
        if m.fade_out:
            mf.append(f"afade=t=out:st={max(0, total - m.fade_out):.3f}:d={m.fade_out:.3f}")
        mix.append(f"[{idx}:a]{','.join(mf)}[mus]")
        if narr_bus and m.duck_under_narration:
            mix.append(f"{narr_bus}asplit=2[narmix][narsc]")
            mix.append("[mus][narsc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[musd]")
            mix_inputs += ["[musd]", "[narmix]"]
            narr_bus = None
        else:
            mix_inputs.append("[mus]")
    if narr_bus:
        mix_inputs.append(narr_bus)
    for k, cue in enumerate(tl.sfx):
        idx = add_input("-i", str(Path(cue.source).resolve()))
        ms = int(cue.start * 1000)
        mix.append(f"[{idx}:a]aresample={o.sample_rate},aformat=channel_layouts=stereo,"
                   f"volume={cue.volume:.3f},adelay={ms}|{ms}[sfx{k}]")
        mix_inputs.append(f"[sfx{k}]")
    if len(mix_inputs) > 1:
        mix.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0:duration=first[premix]")
        last = "[premix]"
    else:
        last = "[bed]"
    afinal = [f"atrim=duration={total:.4f}"]
    if o.loudness_lufs is not None:
        afinal.append(f"loudnorm=I={o.loudness_lufs}:TP=-1.5:LRA=11")
        afinal.append(f"aresample={o.sample_rate}")
    afinal.append("alimiter=limit=0.95:level=disabled")
    mix.append(f"{last}{','.join(afinal)}[aout]")
    graph += mix

    filter_graph = ";\n".join(graph)
    args += ["-filter_complex", filter_graph, "-map", "[vout]", "-map", "[aout]",
             "-c:v", enc, *ff.encoder_quality_args(enc, o.crf, o.preset), "-pix_fmt", "yuv420p",
             "-r", str(fps), "-c:a", o.audio_codec, "-b:a", o.audio_bitrate, "-ar", str(o.sample_rate),
             "-t", f"{total:.4f}", "-movflags", "+faststart", str(output)]
    if any(c.speed != 1.0 for c in tl.clips):
        notes.append("Constant speed changes applied; true speed ramps are not implemented.")
    return RenderPlan(args=args, filter_graph=filter_graph, total_duration=total, encoder=enc, notes=notes)


def render(tl: Timeline, output: Path, work_dir: Path | None = None,
           progress: Callable[[float], None] | None = None, encoder: str | None = None) -> RenderPlan:
    """Render the timeline to *output*. Writes to a temp file first, then moves into place."""
    for p in tl.source_paths():
        if not p.exists():
            raise FileNotFoundError(f"Timeline source missing: {p}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="autocine_render_", dir=work_dir))
    try:
        sub_name = None
        if tl.subtitles and tl.subtitles.burn_in:
            # Copy subtitles next to the cwd and reference by bare name: avoids filter-path
            # escaping problems with Windows drive letters ("C:\...").
            sub_name = "subs" + Path(tl.subtitles.path).suffix.lower()
            shutil.copyfile(tl.subtitles.path, tmp_dir / sub_name)
        tmp_out = tmp_dir / f"render{output.suffix or '.mp4'}"
        plan = build_render_plan(tl, tmp_out, encoder=encoder, subtitle_filename=sub_name)
        args = list(plan.args)
        if len(plan.filter_graph) > 6000:  # stay well under the Windows 32k command-line limit
            script = tmp_dir / "graph.txt"
            script.write_text(plan.filter_graph, encoding="utf-8")
            fi = args.index("-filter_complex")
            args[fi:fi + 2] = ff.filter_script_args(script)
        log.info("Rendering %.2fs timeline (%d clips) with %s", plan.total_duration, len(tl.clips), plan.encoder)
        ff.run_ffmpeg(args, progress=progress, total_seconds=plan.total_duration, cwd=tmp_dir)
        shutil.move(str(tmp_out), output)
        plan.args[-1] = str(output)
        return plan
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
