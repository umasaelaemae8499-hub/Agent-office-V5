"""Environment diagnostics (`python -m app.main doctor`).

Inspects OS, Python, FFmpeg, GPU/VRAM, RAM, disk, CUDA, ComfyUI and provider
credentials, then derives which features can run and what hardware tier applies.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.utils import ffmpeg as ff


@dataclass
class GPUInfo:
    name: str
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    driver: str | None = None
    vendor: str = "unknown"


@dataclass
class DoctorReport:
    os: str
    python: str
    python_ok: bool
    ffmpeg: str | None
    ffprobe: str | None
    ffmpeg_version: str | None
    required_filters_missing: list[str]
    h264_encoders_working: list[str]
    selected_encoder: str | None
    cpu_count: int
    ram_total_gb: float | None
    disk_free_gb: float
    gpus: list[GPUInfo]
    cuda_available: bool | None
    python_packages: dict[str, bool]
    comfyui: dict[str, Any]
    tts_engines: list[str]
    cloud_credentials: dict[str, bool]
    hardware_tier: str
    recommendations: list[str] = field(default_factory=list)
    features: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


REQUIRED_FILTERS = ["xfade", "loudnorm", "sidechaincompress", "blackdetect", "freezedetect",
                    "silencedetect", "acrossfade", "subtitles", "vignette", "zoompan"]
PY_PACKAGES = ["cv2", "scenedetect", "librosa", "numpy", "PIL", "gradio", "pydantic",
               "faster_whisper", "pyttsx3", "torch"]


def ram_total_gb() -> float | None:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return round(stat.ullTotalPhys / 1024**3, 1)
        if Path("/proc/meminfo").exists():
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
        return round(int(out.stdout.strip()) / 1024**3, 1)
    except Exception:
        return None


def detect_gpus() -> list[GPUInfo]:
    gpus: list[GPUInfo] = []
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total,memory.free,driver_version",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 timeout=15)
            for line in out.stdout.strip().splitlines():
                name, total, free, driver = [x.strip() for x in line.split(",")]
                gpus.append(GPUInfo(name, int(float(total)), int(float(free)), driver, "nvidia"))
        except Exception:
            pass
    if not gpus and os.name == "nt":
        # Non-NVIDIA (or driverless) GPUs on Windows. AdapterRAM is capped at 4GB by WMI, so
        # VRAM is reported only as a lower bound.
        try:
            ps = ["powershell", "-NoProfile", "-Command",
                  "Get-CimInstance Win32_VideoController | ForEach-Object { \"$($_.Name)|$($_.AdapterRAM)|$($_.DriverVersion)\" }"]
            out = subprocess.run(ps, capture_output=True, text=True, timeout=20)
            for line in out.stdout.strip().splitlines():
                name, ram, driver = (line.split("|") + ["", ""])[:3]
                vendor = ("amd" if "amd" in name.lower() or "radeon" in name.lower()
                          else "intel" if "intel" in name.lower() else
                          "nvidia" if "nvidia" in name.lower() else "unknown")
                vram = int(ram) // 1024**2 if ram.strip().isdigit() else None
                gpus.append(GPUInfo(name.strip(), vram, None, driver or None, vendor))
        except Exception:
            pass
    return gpus


def cuda_available() -> bool | None:
    """True/False if torch is installed, None if it cannot be determined."""
    if importlib.util.find_spec("torch") is None:
        return None
    try:
        out = subprocess.run([sys.executable, "-c", "import torch;print(torch.cuda.is_available())"],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.strip() == "True"
    except Exception:
        return None


def detect_comfyui(settings: Settings) -> dict[str, Any]:
    cfg = settings.providers.get("comfyui", {})
    url = cfg.get("base_url", "http://127.0.0.1:8188")
    result: dict[str, Any] = {"url": url, "reachable": False, "install_dir": None, "workflows": {}}
    try:
        r = httpx.get(f"{url.rstrip('/')}/system_stats", timeout=3)
        if r.status_code == 200:
            result["reachable"] = True
            result["system_stats"] = r.json()
    except Exception:
        pass
    candidates = [os.environ.get("COMFYUI_DIR", ""), str(Path.home() / "ComfyUI"),
                  "C:/ComfyUI", "C:/ComfyUI_windows_portable/ComfyUI", str(Path.home() / "Desktop/ComfyUI")]
    for c in candidates:
        if c and (Path(c) / "main.py").exists():
            result["install_dir"] = c
            break
    for kind in ("video_workflow", "image_workflow"):
        wf = cfg.get(kind)
        result["workflows"][kind] = bool(wf and settings.resolve(wf).exists())
    return result


def detect_tts() -> list[str]:
    engines = []
    if os.name == "nt" and importlib.util.find_spec("pyttsx3"):
        engines.append("pyttsx3 (Windows SAPI5)")
    for b in ("espeak-ng", "espeak"):
        if shutil.which(b):
            engines.append(b)
            break
    if shutil.which("say"):
        engines.append("say (macOS)")
    if shutil.which("piper"):
        engines.append("piper")
    return engines


def hardware_tier(gpus: list[GPUInfo], ram: float | None) -> str:
    vram = max((g.vram_total_mb or 0) for g in gpus) if gpus else 0
    nvidia = any(g.vendor == "nvidia" for g in gpus)
    if nvidia and vram >= 20000:
        return "high (24GB-class NVIDIA): local video diffusion feasible"
    if nvidia and vram >= 11000:
        return "mid (12-16GB NVIDIA): local image gen + small/quantised video models"
    if nvidia and vram >= 7000:
        return "entry (8GB NVIDIA): local image gen; local video gen only heavily quantised/slow"
    return "cpu (no usable CUDA GPU): editing/analysis only; generation must use a cloud provider"


def run_doctor(settings: Settings, test_encoders: bool = True) -> DoctorReport:
    ffbin, fpbin = ff.ffmpeg_bin(), ff.ffprobe_bin()
    version = None
    if ffbin:
        out = subprocess.run([ffbin, "-version"], capture_output=True, text=True, timeout=15).stdout
        version = out.splitlines()[0] if out else None
    filters = ff.list_filters()
    missing = [f for f in REQUIRED_FILTERS if f not in filters] if ffbin else REQUIRED_FILTERS
    working = []
    if ffbin and test_encoders:
        working = [e for e in ("libx264", *ff.HW_H264_ENCODERS) if ff.encoder_works(e)]
    selected = None
    try:
        selected = ff.select_video_encoder(settings.render.video_codec) if ffbin else None
    except ff.FFmpegError:
        pass
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    gpus = detect_gpus()
    ram = ram_total_gb()
    comfy = detect_comfyui(settings)
    creds = {name: bool(os.environ.get(cfg.get("api_key_env", ""), "").strip())
             for name, cfg in settings.providers.items() if isinstance(cfg, dict) and cfg.get("api_key_env")}
    report = DoctorReport(
        os=f"{platform.system()} {platform.release()} ({platform.version()})",
        python=platform.python_version(), python_ok=sys.version_info >= (3, 11),
        ffmpeg=ffbin, ffprobe=fpbin, ffmpeg_version=version,
        required_filters_missing=missing, h264_encoders_working=working, selected_encoder=selected,
        cpu_count=os.cpu_count() or 1, ram_total_gb=ram,
        disk_free_gb=round(shutil.disk_usage(settings.projects_dir).free / 1024**3, 1),
        gpus=gpus, cuda_available=cuda_available(),
        python_packages={p: importlib.util.find_spec(p) is not None for p in PY_PACKAGES},
        comfyui=comfy, tts_engines=detect_tts(), cloud_credentials=creds,
        hardware_tier=hardware_tier(gpus, ram),
    )
    _derive_features(report, settings)
    return report


def _derive_features(r: DoctorReport, settings: Settings) -> None:
    f, rec = r.features, r.recommendations
    editing_ok = bool(r.ffmpeg and r.ffprobe and "libx264" in r.h264_encoders_working)
    f["existing_video_to_final_video"] = "available" if editing_ok else "blocked: FFmpeg/libx264 missing"
    f["clip_analysis"] = "available" if r.python_packages.get("cv2") and r.python_packages.get("scenedetect") \
        else "partial: install opencv-python-headless + scenedetect"
    f["beat_sync"] = "available" if r.python_packages.get("librosa") else "blocked: pip install librosa"
    f["subtitles_from_script"] = "available" if "subtitles" not in r.required_filters_missing else \
        "partial: SRT only (FFmpeg lacks libass for burn-in)"
    f["speech_transcription"] = "available" if r.python_packages.get("faster_whisper") else \
        "not installed: pip install faster-whisper (optional)"
    f["narration_tts"] = f"available ({r.tts_engines[0]})" if r.tts_engines else \
        "not installed: pip install pyttsx3 (Windows) or install espeak-ng / piper"
    comfy_ready = r.comfyui["reachable"] and r.comfyui["workflows"].get("video_workflow")
    paid_ok = settings.budget.allow_paid_generation and settings.budget.max_project_budget_usd > 0
    if comfy_ready:
        f["text_to_video"] = "available (ComfyUI local)"
    elif r.comfyui["reachable"]:
        f["text_to_video"] = "blocked: ComfyUI running but no API-format video workflow configured"
    else:
        f["text_to_video"] = "blocked: no generation provider configured (ComfyUI offline, cloud adapters unverified)"
    f["planning"] = "available (offline rule-based planner; Claude Code can author plans as JSON)"
    if not paid_ok:
        rec.append("Paid generation disabled (ALLOW_PAID_GENERATION=false / budget 0) - no money can be spent.")
    if not r.python_ok:
        rec.append("Upgrade to Python 3.11+.")
    if not r.ffmpeg:
        rec.append("Install FFmpeg: `winget install Gyan.FFmpeg`, then reopen the terminal.")
    unusable_hw = [e for e in ff.HW_H264_ENCODERS if e in ff.list_encoders()
                   and e not in r.h264_encoders_working] if r.ffmpeg else []
    if unusable_hw:
        rec.append(f"FFmpeg has {', '.join(unusable_hw)} compiled in but they failed a test encode "
                   "(no matching GPU/driver) - software libx264 will be used.")
    tier = r.hardware_tier
    if tier.startswith("high"):
        rec.append("Install ComfyUI + Wan 2.x 14B (fp8, ~15-30GB download) or LTX-Video for local text-to-video.")
    elif tier.startswith("mid"):
        rec.append("Install ComfyUI + Wan 2.x 1.3B / LTX-Video (~6-10GB downloads) for local short clips; "
                   "SDXL/FLUX-schnell (fp8) for stills animated with Ken Burns moves.")
    elif tier.startswith("entry"):
        rec.append("Install ComfyUI + SDXL-Turbo / SD1.5 for stills (~2-7GB); expect local video gen to be slow.")
    else:
        rec.append("No CUDA GPU: use existing footage, or enable a verified cloud provider with an explicit budget.")
    if not comfy_ready and r.comfyui["install_dir"]:
        rec.append(f"ComfyUI found at {r.comfyui['install_dir']} but not running - start it, then export a "
                   "workflow in API format to config/workflows/.")
