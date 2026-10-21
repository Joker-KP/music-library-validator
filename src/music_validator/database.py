"""SQLite metadata cache. One writer, safe SQL parameters, versionable schema."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
 path TEXT PRIMARY KEY, album_dir TEXT NOT NULL,
 size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 metadata_json TEXT NOT NULL, error TEXT, scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_dir);
CREATE TABLE IF NOT EXISTS scans (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT, files_seen INTEGER, files_read INTEGER, errors INTEGER
);
"""

def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get(conn, path):
    return conn.execute("SELECT * FROM tracks WHERE path=?", (str(path),)).fetchone()


def upsert(conn, path, album_dir, stat, metadata, error=None):
    conn.execute("""INSERT INTO tracks(path,album_dir,size,mtime_ns,metadata_json,error)
    VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET album_dir=excluded.album_dir,
    size=excluded.size,mtime_ns=excluded.mtime_ns,metadata_json=excluded.metadata_json,
    error=excluded.error,scanned_at=CURRENT_TIMESTAMP""",
    (str(path), str(album_dir), stat.st_size, stat.st_mtime_ns, json.dumps(metadata, ensure_ascii=False), error))


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM tracks ORDER BY album_dir,path")]
