"""Project persistence: SQLite rows for state + atomically-written JSON documents per project."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.database.models import SCHEMA
from app.utils.paths import atomic_write_json, ensure_within, slugify

STAGE_ORDER = ["plan", "storyboard", "ingest", "generate", "analyze", "timeline",
               "audio", "render", "validate", "report"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    def __init__(self, db_path: Path, projects_dir: Path) -> None:
        self.db_path = db_path
        self.projects_dir = projects_dir
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        projects_dir.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with self._lock:
                yield conn
                conn.commit()
        finally:
            conn.close()

    # ---------- projects ----------
    def create_project(self, user_request: str, mode: str, name: str | None = None,
                       exec_mode: str = "hybrid", budget_usd: float = 0.0) -> str:
        pid = f"{slugify(name or user_request, 30)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        with self._conn() as c:
            c.execute("INSERT INTO projects (id,name,mode,user_request,exec_mode,budget_usd,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (pid, name or slugify(user_request, 30), mode, user_request, exec_mode, budget_usd, now, now))
        self.project_dir(pid).mkdir(parents=True, exist_ok=True)
        return pid

    def project_dir(self, project_id: str) -> Path:
        if not project_id or any(ch in project_id for ch in "/\\:") or project_id.startswith("."):
            raise ValueError(f"Invalid project id: {project_id!r}")
        return ensure_within(project_id, self.projects_dir)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def require_project(self, project_id: str) -> dict[str, Any]:
        p = self.get_project(project_id)
        if not p:
            raise KeyError(f"Project not found: {project_id}")
        return p

    def list_projects(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    _UPDATABLE = {"status", "current_stage", "estimated_cost_usd", "actual_cost_usd", "output_path",
                  "paid_approved", "budget_usd", "exec_mode", "name"}

    def update_project(self, project_id: str, **fields: Any) -> None:
        bad = set(fields) - self._UPDATABLE
        if bad:
            raise ValueError(f"Cannot update fields: {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE projects SET {cols}, updated_at=? WHERE id=?",
                      (*fields.values(), _now(), project_id))

    # ---------- stages ----------
    def set_stage(self, project_id: str, stage: str, status: str, detail: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO stages (project_id,stage,status,detail,updated_at) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(project_id,stage) DO UPDATE SET status=excluded.status, "
                      "detail=excluded.detail, updated_at=excluded.updated_at",
                      (project_id, stage, status, detail, _now()))
        self.update_project(project_id, current_stage=stage)

    def stages(self, project_id: str) -> dict[str, dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM stages WHERE project_id=?", (project_id,)).fetchall()
        return {r["stage"]: dict(r) for r in rows}

    def stage_done(self, project_id: str, stage: str) -> bool:
        return self.stages(project_id).get(stage, {}).get("status") == "done"

    def reset_stages_from(self, project_id: str, stage: str) -> None:
        later = STAGE_ORDER[STAGE_ORDER.index(stage):]
        with self._conn() as c:
            c.executemany("DELETE FROM stages WHERE project_id=? AND stage=?", [(project_id, s) for s in later])

    # ---------- documents (plan, storyboard, timeline, reports) ----------
    def save_doc(self, project_id: str, name: str, data: Any) -> Path:
        path = self.project_dir(project_id) / f"{name}.json"
        atomic_write_json(path, data)
        return path

    def load_doc(self, project_id: str, name: str) -> Any | None:
        path = self.project_dir(project_id) / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    # ---------- generation jobs ----------
    def find_succeeded_job(self, request_hash: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM generation_jobs WHERE request_hash=? AND status='succeeded' "
                            "ORDER BY id DESC LIMIT 1", (request_hash,)).fetchone()
        return dict(row) if row else None

    def create_job(self, project_id: str, scene_number: int, provider: str, request_hash: str,
                   request: dict[str, Any]) -> int:
        now = _now()
        with self._conn() as c:
            cur = c.execute("INSERT INTO generation_jobs (project_id,scene_number,provider,request_hash,"
                            "request_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                            (project_id, scene_number, provider, request_hash, json.dumps(request),
                             "queued", now, now))
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        allowed = {"status", "attempts", "cost_usd", "output_path", "error", "response_json"}
        if set(fields) - allowed:
            raise ValueError("bad job fields")
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE generation_jobs SET {cols}, updated_at=? WHERE id=?",
                      (*fields.values(), _now(), job_id))

    def jobs(self, project_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM generation_jobs WHERE project_id=? ORDER BY id",
                                               (project_id,))]

    # ---------- assets ----------
    def add_asset(self, project_id: str | None, kind: str, path: str, sha256: str | None = None,
                  source: str | None = None, license: str | None = None,
                  metadata: dict[str, Any] | None = None) -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO assets (project_id,kind,path,sha256,source,license,metadata_json,created_at)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (project_id, kind, path, sha256, source, license,
                             json.dumps(metadata or {}), _now()))
            return int(cur.lastrowid)

    def assets(self, project_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM assets WHERE project_id=?", [project_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY id", args)]

    # ---------- events ----------
    def log_event(self, project_id: str | None, message: str, level: str = "info",
                  stage: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events (project_id,level,stage,message,created_at) VALUES (?,?,?,?,?)",
                      (project_id, level, stage, message, _now()))

    def events(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT ?",
                                               (project_id, limit))][::-1]
