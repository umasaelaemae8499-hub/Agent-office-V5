"""Filesystem safety helpers: filename sanitising, path containment, atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


class UnsafePathError(ValueError):
    pass


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Return a filename safe on Windows and POSIX (no separators, no reserved names)."""
    name = unicodedata.normalize("NFKC", Path(str(name).replace("\\", "/")).name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    name = f"{stem}.{ext}" if ext else stem
    return name[:max_length]


def slugify(text: str, max_length: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return (text or "project")[:max_length].strip("_") or "project"


def ensure_within(path: Path | str, root: Path | str) -> Path:
    """Resolve *path* and verify it lies inside *root* (prevents path traversal)."""
    root_r = Path(root).resolve()
    p = Path(path)
    resolved = (p if p.is_absolute() else root_r / p).resolve()
    if resolved != root_r and root_r not in resolved.parents:
        raise UnsafePathError(f"{path!s} escapes allowed directory {root_r}")
    return resolved


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def content_hash(obj: Any) -> str:
    """Stable hash of a JSON-serialisable object (used as a generation cache key)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
