import pytest

from app.quality.validator import Expectations, validate_file
from app.utils.ffmpeg import probe, select_video_encoder
from app.video.analyzer import analyze_clip
from app.video.renderer import build_render_plan, render
from app.video.timeline import (AudioCue, Effects, MusicTrack, OutputSpec, SubtitleTrack, Timeline, Transition,
                                VideoClip, fit_durations_to_target, fit_durations_with_caps)


def test_timeline_duration_math():
    tl = Timeline(clips=[VideoClip(source="a", out_point=3),
                         VideoClip(source="b", out_point=4, transition_in=Transition(type="crossfade", duration=1)),
                         VideoClip(source="c", in_point=1, out_point=3, speed=2.0)])
    assert tl.total_duration == pytest.approx(3 + 4 - 1 + 1)
    assert tl.clip_start_times() == pytest.approx([0, 2, 6])


def test_timeline_rejects_oversized_transition():
    with pytest.raises(ValueError):
        Timeline(clips=[VideoClip(source="a", out_point=1),
                        VideoClip(source="b", out_point=3, transition_in=Transition(type="dissolve", duration=1.5))])


def test_first_clip_transition_forced_to_cut_and_even_dims():
    tl = Timeline(clips=[VideoClip(source="a", out_point=2, transition_in=Transition(type="dissolve", duration=0.5))],
                  output=OutputSpec(width=1081, height=1921))
    assert tl.clips[0].transition_in.type == "cut"
    assert (tl.output.width, tl.output.height) == (1080, 1920)


@pytest.mark.parametrize("target", [6.0, 15.0, 30.0])
def test_fit_durations(target):
    trans = [0, 0.5, 0.5, 0]
    d = fit_durations_to_target([3, 3, 3, 3], trans, target)
    assert sum(d) - sum(trans[1:]) == pytest.approx(target, abs=0.01)
    capped = fit_durations_with_caps([3, 3, 3, 3], trans, target, caps=[2, 100, 100, 100])
    assert capped[0] <= 2 and sum(capped) - 1.0 == pytest.approx(target, abs=0.01)


def test_render_full_feature_timeline(media_dir, tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:02,000\nHello\n", encoding="utf-8")
    tl = Timeline(
        clips=[VideoClip(source=str(media_dir / "a.mp4"), in_point=0.5, out_point=3, effects=Effects(zoom=0.1),
                         color="cinematic_teal_orange"),
               VideoClip(source=str(media_dir / "b.mp4"), out_point=2.5, speed=1.25,
                         transition_in=Transition(type="dissolve", duration=0.5), effects=Effects(pan="up", vignette=True)),
               VideoClip(source=str(media_dir / "c.mp4"), out_point=2, transition_in=Transition(type="cut")),
               VideoClip(source=str(media_dir / "still.png"), kind="image", out_point=2,
                         transition_in=Transition(type="fade_black", duration=0.4), effects=Effects(zoom=-0.1))],
        music=MusicTrack(source=str(media_dir / "music.wav"), volume=0.4),
        narration=[AudioCue(source=str(media_dir / "voice.wav"), start=1.0)],
        sfx=[AudioCue(source=str(media_dir / "voice.wav"), start=4.0, volume=0.2)],
        subtitles=SubtitleTrack(path=str(srt)),
        output=OutputSpec(width=360, height=640, fps=25, preset="ultrafast", fade_out_to_black=0.5))
    out = tmp_path / "out.mp4"
    render(tl, out)
    info = probe(out)
    assert (info.width, info.height) == (360, 640)
    assert info.fps == pytest.approx(25)
    assert info.duration == pytest.approx(tl.total_duration, abs=0.08)
    assert info.has_audio and info.audio_codec == "aac"
    rep = validate_file(out, Expectations(duration=tl.total_duration, width=360, height=640, fps=25,
                                          allowed_black_tail=0.5, max_black_seconds=0.9))
    assert rep.passed, rep.summary()


def test_render_plan_is_deterministic(media_dir, tmp_path):
    tl = Timeline(clips=[VideoClip(source=str(media_dir / "a.mp4"), out_point=2),
                         VideoClip(source=str(media_dir / "c.mp4"), out_point=2,
                                   transition_in=Transition(type="crossfade", duration=0.5))],
                  output=OutputSpec(width=320, height=320))
    a = build_render_plan(tl, tmp_path / "x.mp4", encoder="libx264")
    b = build_render_plan(tl, tmp_path / "x.mp4", encoder="libx264")
    assert a.args == b.args and "xfade=transition=fade" in a.filter_graph


def test_render_rejects_out_of_range(media_dir, tmp_path):
    tl = Timeline(clips=[VideoClip(source=str(media_dir / "c.mp4"), out_point=10)])
    with pytest.raises(ValueError):
        render(tl, tmp_path / "x.mp4")


def test_encoder_selection_falls_back_to_software():
    assert select_video_encoder("auto") in {"libx264", "h264_nvenc", "h264_qsv", "h264_amf"}


def test_analyzer_detects_black_and_freeze(media_dir):
    black = analyze_clip(media_dir / "black.mp4")
    assert black.black_intervals and black.score < 0.1 and "mostly black" in black.warnings
    frozen = analyze_clip(media_dir / "frozen.mp4")
    assert frozen.freeze_intervals and frozen.freeze_intervals[0][0] == pytest.approx(0, abs=0.1)
    moving = analyze_clip(media_dir / "a.mp4", frames_dir=media_dir / "frames")
    assert moving.score > black.score
    assert moving.segments[0].keyframe and moving.semantic_analysis.startswith("not performed")
    assert not moving.silence_intervals  # has a continuous tone


def test_validator_flags_wrong_specs(media_dir):
    rep = validate_file(media_dir / "black.mp4", Expectations(duration=10, width=1080, height=1920, fps=30))
    failed = {c.name for c in rep.errors}
    assert not rep.passed
    assert {"duration", "resolution", "has_audio_stream", "unexpected_black_frames"} <= failed


def test_validator_missing_file(tmp_path):
    rep = validate_file(tmp_path / "nope.mp4", Expectations())
    assert not rep.passed and rep.checks[0].name == "file_exists"


def test_long_filter_graph_uses_script_file(media_dir, tmp_path):
    # 14 clips with transitions -> graph > 6000 chars -> must go through a script file
    clips = [VideoClip(source=str(media_dir / ("a.mp4" if i % 2 else "c.mp4")), out_point=1.0, color="warm",
                       effects=Effects(zoom=0.05, vignette=True),
                       transition_in=Transition(type="crossfade" if i else "cut", duration=0.2 if i else 0))
             for i in range(14)]
    tl = Timeline(clips=clips, output=OutputSpec(width=160, height=160, preset="ultrafast"))
    assert len(build_render_plan(tl, tmp_path / "x.mp4", encoder="libx264").filter_graph) > 6000
    render(tl, tmp_path / "long.mp4")
    assert probe(tmp_path / "long.mp4").duration == pytest.approx(tl.total_duration, abs=0.08)
