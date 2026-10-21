"""Versioned SQLite cache; one explicit transaction owns a complete scan."""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
PARSER_VERSION = 4


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=0)
    conn.row_factory = sqlite3.Row
    try:
        # No executescript: its implicit commit would break scan rollback.
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"Unsupported cache schema {version}; maximum is {SCHEMA_VERSION}")
        if version == 0:
            conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
                path TEXT PRIMARY KEY, album_dir TEXT NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                metadata_json TEXT NOT NULL, error TEXT,
                scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
            if "parser_version" not in columns:
                conn.execute(
                    "ALTER TABLE tracks ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 0"
                )
            if "last_seen_scan" not in columns:
                conn.execute(
                    "ALTER TABLE tracks ADD COLUMN last_seen_scan INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_dir)")
            conn.execute("""CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT, files_seen INTEGER, files_read INTEGER, errors INTEGER)""")
            conn.execute("CREATE TABLE cache_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return conn
    except BaseException:
        conn.rollback()
        conn.close()
        raise


def bind_library(conn, root: Path, extensions):
    info = root.stat()
    identity = json.dumps(
        {
            "root": str(root),
            "extensions": sorted(extensions),
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        sort_keys=True,
    )
    old = conn.execute("SELECT value FROM cache_settings WHERE key='library_scope'").fetchone()
    if old and old[0] != identity:
        raise ValueError(
            "Cache belongs to a different library scope (root, device/inode or extensions); "
            "use a separate --db. --full-scan does not rebind a cache."
        )
    if not old:
        for row in conn.execute("SELECT path FROM tracks"):
            path = Path(row[0])
            if not path.is_relative_to(root) or path.suffix.lower() not in extensions:
                raise ValueError(
                    "Legacy cache contains a different library scope; use a separate --db"
                )
        conn.execute("INSERT INTO cache_settings(key,value) VALUES('library_scope',?)", (identity,))


def get(conn, path):
    return conn.execute("SELECT * FROM tracks WHERE path=?", (str(path),)).fetchone()


def upsert(conn, path, album_dir, stat, metadata, error=None, scan_id=0):
    conn.execute(
        """INSERT INTO tracks
        (path,album_dir,size,mtime_ns,metadata_json,error,parser_version,last_seen_scan)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
        album_dir=excluded.album_dir,size=excluded.size,mtime_ns=excluded.mtime_ns,
        metadata_json=excluded.metadata_json,error=excluded.error,
        parser_version=excluded.parser_version,last_seen_scan=excluded.last_seen_scan,
        scanned_at=CURRENT_TIMESTAMP""",
        (
            str(path),
            str(album_dir),
            stat.st_size,
            stat.st_mtime_ns,
            json.dumps(metadata, ensure_ascii=False),
            error,
            PARSER_VERSION,
            scan_id,
        ),
    )


def iter_rows(conn):
    for row in conn.execute("SELECT * FROM tracks ORDER BY album_dir,path"):
        yield dict(row)


def rows(conn):
    return list(iter_rows(conn))
