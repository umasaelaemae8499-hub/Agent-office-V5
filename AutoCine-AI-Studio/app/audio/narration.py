"""Offline text-to-speech narration.

Backends (auto-detected, first available wins):
  * pyttsx3  - Windows SAPI5 voices (pip install pyttsx3). Fully offline.
  * espeak-ng / espeak - offline, robotic but dependable (Linux/Windows builds exist).
  * piper    - offline neural TTS if the `piper` binary + a voice model are installed.
No backend -> narration is reported as unavailable; nothing is faked.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from app.utils import ffmpeg as ff

log = logging.getLogger(__name__)


class TTSBackend(Protocol):
    name: str

    def synthesize(self, text: str, out_wav: Path, voice: str | None = None, rate: float = 1.0) -> Path: ...


class Pyttsx3Backend:
    name = "pyttsx3"

    def synthesize(self, text: str, out_wav: Path, voice: str | None = None, rate: float = 1.0) -> Path:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", int(175 * rate))
        if voice:
            for v in engine.getProperty("voices"):
                if voice.lower() in (v.name or "").lower() or voice == v.id:
                    engine.setProperty("voice", v.id)
                    break
        engine.save_to_file(text, str(out_wav))
        engine.runAndWait()
        return out_wav


class EspeakBackend:
    def __init__(self, binary: str) -> None:
        self.binary = binary
        self.name = Path(binary).stem

    def synthesize(self, text: str, out_wav: Path, voice: str | None = None, rate: float = 1.0) -> Path:
        args = [self.binary, "-w", str(out_wav), "-s", str(int(165 * rate))]
        if voice:
            args += ["-v", voice]
        subprocess.run([*args, text], check=True, capture_output=True, timeout=120)
        return out_wav


class PiperBackend:
    name = "piper"

    def __init__(self, binary: str, model: str) -> None:
        self.binary, self.model = binary, model

    def synthesize(self, text: str, out_wav: Path, voice: str | None = None, rate: float = 1.0) -> Path:
        subprocess.run([self.binary, "--model", voice or self.model, "--output_file", str(out_wav),
                        "--length_scale", f"{1 / rate:.3f}"], input=text, text=True, check=True,
                       capture_output=True, timeout=300)
        return out_wav


def get_tts_backend() -> TTSBackend | None:
    if os.name == "nt" and importlib.util.find_spec("pyttsx3"):
        return Pyttsx3Backend()
    piper, model = shutil.which("piper"), os.environ.get("PIPER_MODEL", "")
    if piper and model and Path(model).exists():
        return PiperBackend(piper, model)
    for b in ("espeak-ng", "espeak"):
        if path := shutil.which(b):
            return EspeakBackend(path)
    return None


def synthesize_lines(lines: list[tuple[int, str]], out_dir: Path, backend: TTSBackend,
                     voice: str | None = None, rate: float = 1.0) -> list[tuple[int, Path, float]]:
    """Synthesize (scene_number, text) pairs -> [(scene_number, wav_path, duration_seconds)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for scene_no, text in lines:
        wav = out_dir / f"narration_scene{scene_no:02d}.wav"
        if not wav.exists():
            backend.synthesize(text, wav, voice=voice, rate=rate)
        out.append((scene_no, wav, ff.probe(wav).duration))
    return out
