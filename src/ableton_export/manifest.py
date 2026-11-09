"""Durable manifest and export state, with transactions per set."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def database(path: str):
    """Open the local state database and initialize its schema."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, mtime_ns INTEGER,
      source_hash TEXT, xml_hash TEXT, status TEXT NOT NULL,
      record TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audio (
      path TEXT PRIMARY KEY, name TEXT, size INTEGER, mtime_ns INTEGER
    );
    CREATE INDEX IF NOT EXISTS audio_name_size ON audio(name,size);
    CREATE TABLE IF NOT EXISTS events (
      sequence INTEGER PRIMARY KEY, project_id TEXT, step TEXT, details TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def event(db, project_id: str, step: str, **details):
    """Persist a named state transition and its diagnostic context."""
    db.execute(
        "INSERT INTO events(project_id,step,details) VALUES (?,?,?)",
        (project_id, step, json.dumps(details, default=str)),
    )
    db.commit()


def put_project(db, record: dict, status: str):
    """Upsert one source record without losing a completed render."""
    prior = db.execute(
        "SELECT source_hash,status FROM projects WHERE id=?", (record["id"],)
    ).fetchone()
    if (
        prior
        and prior["source_hash"] == record["source_hash"]
        and prior["status"] in {"complete", "prepared"}
    ):
        status = prior["status"]
    db.execute(
        """INSERT INTO projects(id,path,mtime_ns,source_hash,xml_hash,status,record)
      VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      mtime_ns=excluded.mtime_ns,source_hash=excluded.source_hash,xml_hash=excluded.xml_hash,
      status=excluded.status,record=excluded.record,updated_at=CURRENT_TIMESTAMP""",
        (
            record["id"],
            record["path"],
            record["mtime_ns"],
            record["source_hash"],
            record["xml_hash"],
            status,
            json.dumps(record),
        ),
    )
    db.commit()


def write_json(path: Path, data):
    """Atomically publish a report or queue snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, default=str) + "\n")
    temporary.replace(path)
