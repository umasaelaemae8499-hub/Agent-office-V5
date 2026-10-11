"""SQLite schema for persistent project state."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,                 -- text_to_video | video_to_video
    user_request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    current_stage TEXT,
    exec_mode TEXT NOT NULL DEFAULT 'hybrid',
    budget_usd REAL NOT NULL DEFAULT 0,
    paid_approved INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0,
    output_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,              -- pending | running | done | failed | blocked
    detail TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, stage)
);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scene_number INTEGER NOT NULL,
    provider TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT,
    status TEXT NOT NULL,              -- queued | running | succeeded | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    output_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_hash ON generation_jobs(request_hash, status);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                -- source_video | generated_video | image | music | sfx | narration | render
    path TEXT NOT NULL,
    sha256 TEXT,
    source TEXT,                       -- user_upload | comfyui | local_library | tts | ...
    license TEXT,                      -- provenance / licensing note
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    stage TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
