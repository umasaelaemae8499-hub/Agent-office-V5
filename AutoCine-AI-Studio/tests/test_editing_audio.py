import pytest

from app.audio.music import analyze_music, snap_cuts
from app.audio.sound_effects import load_library, match
from app.subtitles.subtitle_engine import Cue, write_ass, write_srt
from app.video.analyzer import analyze_clip
from app.video.editor import build_edit_timeline


@pytest.fixture(scope="module")
def analyses(media_dir):
    return [analyze_clip(media_dir / f) for f in ("a.mp4", "b.mp4", "c.mp4", "black.mp4", "frozen.mp4")]


def test_music_tempo_and_beats(media_dir):
    m = analyze_music(media_dir / "music.wav")
    assert 110 <= m.tempo_bpm <= 130
    gaps = [b - a for a, b in zip(m.beats, m.beats[1:])]
    assert sum(gaps) / len(gaps) == pytest.approx(0.5, abs=0.05)


def test_snap_strength_depends_on_style(media_dir):
    m = analyze_music(media_dir / "music.wav")
    cuts = [0, 1.9, 4.3, 6.1, 8.2, 10.0]
    fast = snap_cuts(cuts, m, "fast_paced")
    doc = snap_cuts(cuts, m, "documentary")
    moved = lambda xs: sum(abs(a - b) > 1e-3 for a, b in zip(cuts, xs))  # noqa: E731
    assert moved(fast) > moved(doc)
    assert fast[0] == 0 and fast[-1] == 10.0  # endpoints never move


@pytest.mark.parametrize("style", ["fast_paced", "cinematic", "documentary", "product_commercial"])
@pytest.mark.parametrize("target", [8.0, 20.0])
def test_editor_hits_target_and_skips_black(analyses, media_dir, style, target):
    m = analyze_music(media_dir / "music.wav")
    tl, notes = build_edit_timeline(analyses, target, style, "9:16", m, str(media_dir / "music.wav"))
    assert tl.total_duration == pytest.approx(target, abs=0.02)
    assert all("black.mp4" not in c.source for c in tl.clips)
    assert (tl.output.width, tl.output.height) == (1080, 1920)


def test_editor_is_deterministic(analyses):
    a, _ = build_edit_timeline(analyses, 12, "cinematic", "16:9")
    b, _ = build_edit_timeline(analyses, 12, "cinematic", "16:9")
    assert a.model_dump() == b.model_dump()


def test_editor_no_usable_footage(media_dir):
    with pytest.raises(ValueError):
        build_edit_timeline([analyze_clip(media_dir / "black.mp4")], 10)


def test_sfx_library_matching(tmp_path):
    (tmp_path / "deep_whoosh.wav").write_bytes(b"x")
    (tmp_path / "click_soft.wav").write_bytes(b"x")
    (tmp_path / "library.json").write_text('[{"file": "click_soft.wav", "tags": ["click", "mechanical"], '
                                           '"license": "CC0"}]')
    lib = load_library(tmp_path)
    assert match(lib, "subtle mechanical clicks").path.name == "click_soft.wav"
    assert match(lib, "deep whoosh impact").license.startswith("unknown")


def test_subtitle_files(tmp_path):
    cues = [Cue(0.5, 3.0, "Microplastics are everywhere, from the deepest oceans to the air we breathe.")]
    srt = write_srt(cues, tmp_path / "s.srt").read_text()
    assert "00:00:00,500 -->" in srt
    ass = write_ass(cues, tmp_path / "s.ass", 1080, 1920, "social_media").read_text()
    assert "PlayResY: 1920" in ass and ",422,1" in ass  # 22% bottom safe margin for vertical
    assert "MICROPLASTICS" in ass
