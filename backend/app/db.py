"""Хранилище на SQLite. Сложные структуры лежат в JSON-колонках."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS dialogs (
    id TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    channel TEXT,
    language TEXT,
    audio_url TEXT,
    transcript TEXT NOT NULL,
    raw TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    comment TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(slug, version)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id TEXT NOT NULL REFERENCES dialogs(id),
    checklist_id INTEGER NOT NULL REFERENCES checklists(id),
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    total REAL,
    final_total REAL,
    gross_error INTEGER,
    needs_review INTEGER,
    model TEXT,
    cost_usd REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_dialog ON evaluations(dialog_id, created_at);
CREATE TABLE IF NOT EXISTS appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id),
    criterion_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    author TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    original TEXT NOT NULL,
    decision TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS gold_labels (
    dialog_id TEXT NOT NULL,
    checklist_slug TEXT NOT NULL,
    labels TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dialog_id, checklist_slug)
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def reset_for_tests(path: str) -> None:
    global _conn
    _conn = None
    settings.db_path = Path(path)


def q(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: tuple = ()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------- dialogs ----------

def upsert_dialog(dataset: str, rec: dict[str, Any]) -> None:
    ex(
        """INSERT INTO dialogs(id, dataset, channel, language, audio_url, transcript, raw, imported_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET dataset=excluded.dataset, channel=excluded.channel,
             language=excluded.language, audio_url=excluded.audio_url, transcript=excluded.transcript,
             raw=excluded.raw, imported_at=excluded.imported_at""",
        (rec["conversation_id"], dataset, rec.get("channel"), rec.get("language"), rec.get("audio_url"),
         dumps(rec.get("transcript") or []), dumps(rec), now()),
    )


def get_dialog(dialog_id: str) -> dict[str, Any] | None:
    d = one("SELECT * FROM dialogs WHERE id=?", (dialog_id,))
    if d:
        d["transcript"] = json.loads(d["transcript"])
        d["conversation_id"] = d["id"]
        d.pop("raw", None)
    return d


# ---------- checklists ----------

def save_checklist(data: dict[str, Any], comment: str = "", activate: bool = True) -> int:
    slug = data["slug"]
    with _lock:
        row = one("SELECT MAX(version) AS v FROM checklists WHERE slug=?", (slug,))
        version = (row["v"] or 0) + 1 if row else 1
        data = {**data, "version": version}
        cid = ex("INSERT INTO checklists(slug, version, name, data, comment, is_active, created_at) VALUES(?,?,?,?,?,?,?)",
                 (slug, version, data["name"], dumps(data), comment, 0, now()))
        if activate:
            activate_checklist(cid)
        return cid


def activate_checklist(cid: int) -> None:
    row = one("SELECT slug FROM checklists WHERE id=?", (cid,))
    if not row:
        return
    with _lock:
        ex("UPDATE checklists SET is_active=0 WHERE slug=?", (row["slug"],))
        ex("UPDATE checklists SET is_active=1 WHERE id=?", (cid,))


def get_checklist(cid: int) -> dict[str, Any] | None:
    r = one("SELECT * FROM checklists WHERE id=?", (cid,))
    if r:
        r["data"] = json.loads(r["data"])
    return r


def list_checklists() -> list[dict[str, Any]]:
    return q("SELECT id, slug, version, name, comment, is_active, created_at FROM checklists ORDER BY slug, version DESC")


def active_checklist() -> dict[str, Any] | None:
    r = one("SELECT id FROM checklists WHERE is_active=1 ORDER BY created_at DESC LIMIT 1")
    return get_checklist(r["id"]) if r else None
