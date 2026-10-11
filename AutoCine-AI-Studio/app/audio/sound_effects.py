"""Local audio libraries (music + SFX) with licence provenance.

Each library folder may contain `library.json`:
  [{"file": "whoosh_01.wav", "tags": ["whoosh", "transition"], "license": "CC0 - freesound.org/s/123"}]
Files without an entry are still usable but marked licence "unknown - user supplied".
Nothing is downloaded from the internet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.paths import AUDIO_EXTENSIONS


@dataclass
class LibraryItem:
    path: Path
    tags: list[str] = field(default_factory=list)
    license: str = "unknown - user supplied"


def load_library(folder: Path) -> list[LibraryItem]:
    if not folder.exists():
        return []
    manifest: dict[str, dict] = {}
    mf = folder / "library.json"
    if mf.exists():
        for entry in json.loads(mf.read_text(encoding="utf-8")):
            manifest[entry["file"]] = entry
    items = []
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() in AUDIO_EXTENSIONS:
            rel = str(f.relative_to(folder)).replace("\\", "/")
            meta = manifest.get(rel, {})
            tags = meta.get("tags") or re.split(r"[_\-\s]+", f.stem.lower())
            items.append(LibraryItem(f, [t.lower() for t in tags], meta.get("license", "unknown - user supplied")))
    return items


def match(items: list[LibraryItem], description: str) -> LibraryItem | None:
    """Best keyword overlap between a description ('deep whoosh impact') and item tags."""
    words = set(re.findall(r"[a-z]+", description.lower()))
    best, best_score = None, 0
    for it in items:
        score = len(words & set(it.tags))
        if score > best_score:
            best, best_score = it, score
    return best


def pick_music(items: list[LibraryItem], style_text: str) -> LibraryItem | None:
    return match(items, style_text) or (items[0] if items else None)
