"""Versioned SQLite cache; one explicit transaction owns a complete scan."""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2
PARSER_VERSION = 5


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
        if version < 2:
            conn.execute("""CREATE TABLE audio_fingerprints (
                path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                backend_version TEXT NOT NULL, duration REAL, fingerprint_json TEXT,
                error TEXT
            )""")
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


def audio_fingerprint(conn, path):
    return conn.execute("SELECT * FROM audio_fingerprints WHERE path=?", (str(path),)).fetchone()


def save_audio_fingerprint(conn, path, info, version, duration, fingerprint, error):
    conn.execute(
        """INSERT INTO audio_fingerprints VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
        backend_version=excluded.backend_version,duration=excluded.duration,
        fingerprint_json=excluded.fingerprint_json,error=excluded.error""",
        (
            str(path),
            info.st_size,
            info.st_mtime_ns,
            version,
            duration,
            json.dumps(fingerprint) if fingerprint is not None else None,
            error,
        ),
    )


def prune_audio_fingerprints(conn):
    conn.execute("""DELETE FROM audio_fingerprints WHERE NOT EXISTS (
        SELECT 1 FROM tracks WHERE tracks.path=audio_fingerprints.path
        AND tracks.size=audio_fingerprints.size AND tracks.mtime_ns=audio_fingerprints.mtime_ns
    )""")


def iter_audio_rows(conn):
    for row in conn.execute("""SELECT tracks.*,audio_fingerprints.duration,
        audio_fingerprints.fingerprint_json,audio_fingerprints.error AS fingerprint_error
        FROM tracks JOIN audio_fingerprints USING(path) ORDER BY tracks.path"""):
        yield dict(row)


class AudioIndex:
    """Connection-local audio search projection; tags never influence candidates."""

    def __init__(self, conn, rows, fields):
        from .audio_identity import landmarks

        self.conn = conn
        self.count = 0
        conn.execute("""CREATE TEMP TABLE audio_candidates (
            path TEXT PRIMARY KEY, album_dir TEXT NOT NULL, duration REAL NOT NULL,
            fingerprint_json TEXT NOT NULL, values_json TEXT NOT NULL
        )""")
        conn.execute("""CREATE TEMP TABLE audio_landmarks (
            token INTEGER NOT NULL, path TEXT NOT NULL,
            PRIMARY KEY(token,path)
        ) WITHOUT ROWID""")
        for row in rows:
            tags = row["metadata"].get("tags", {})
            conn.execute(
                "INSERT INTO audio_candidates VALUES(?,?,?,?,?)",
                (
                    row["path"],
                    row["album_dir"],
                    row["duration"],
                    json.dumps(row["fingerprint"]),
                    json.dumps(
                        {field: tags.get(field, "").strip() for field in fields}, ensure_ascii=False
                    ),
                ),
            )
            # Keep every fourth adjacent pair. At >=.95 similarity and >=32
            # overlapping frames at least one of these pairs must survive.
            tokens = {
                token for token, position in landmarks(row["fingerprint"]) if position % 4 == 0
            }
            conn.executemany(
                "INSERT INTO audio_landmarks VALUES(?,?)",
                ((token, row["path"]) for token in sorted(tokens)),
            )
            self.count += 1

    @staticmethod
    def _decode(row):
        row = dict(row)
        row["fingerprint"] = json.loads(row.pop("fingerprint_json"))
        row["values"] = json.loads(row.pop("values_json"))
        return row

    def rows(self):
        for row in self.conn.execute("SELECT * FROM audio_candidates ORDER BY path"):
            yield self._decode(row)

    def get(self, path):
        return self._decode(
            self.conn.execute("SELECT * FROM audio_candidates WHERE path=?", (path,)).fetchone()
        )

    def candidate_paths(self, row, matching):
        from .audio_identity import landmarks

        tolerance = matching["duration_tolerance_seconds"]
        limits = (row["path"], row["duration"] - tolerance, row["duration"] + tolerance)
        if matching["similarity_threshold"] < 0.95:
            for found in self.conn.execute(
                "SELECT path FROM audio_candidates WHERE path<? AND duration BETWEEN ? AND ? ORDER BY path",
                limits,
            ):
                yield found[0]
            return
        tokens = sorted({token for token, _ in landmarks(row["fingerprint"])})
        found = set()
        # Bound parameters for SQLite builds with the older 999-variable limit.
        for start in range(0, len(tokens), 400):
            batch = tokens[start : start + 400]
            placeholders = ",".join("?" for _ in batch)
            found.update(
                item[0]
                for item in self.conn.execute(
                    f"""SELECT DISTINCT c.path FROM audio_landmarks AS l
                JOIN audio_candidates AS c ON c.path=l.path
                WHERE c.path<? AND c.duration BETWEEN ? AND ? AND l.token IN ({placeholders})""",
                    (*limits, *batch),
                )
            )
        yield from sorted(found)


def iter_cross_album_rows(conn, candidates):
    """Sort a temporary credit projection without changing the persistent schema.

    The connection owns this table; it disappears on close, including on failure.
    Input contains normalized identity strings and a mapping of compared values.
    """
    conn.execute("""CREATE TEMP TABLE cross_album_tracks (
        title TEXT NOT NULL, artist TEXT NOT NULL,
        album_dir TEXT NOT NULL, path TEXT NOT NULL,
        values_json TEXT NOT NULL
    )""")
    conn.executemany(
        """INSERT INTO cross_album_tracks VALUES
        (:title,:artist,:album_dir,:path,:values_json)""",
        (
            {**candidate, "values_json": json.dumps(candidate["values"], ensure_ascii=False)}
            for candidate in candidates
        ),
    )
    for row in conn.execute(
        "SELECT * FROM cross_album_tracks ORDER BY title,artist,album_dir,path"
    ):
        result = dict(row)
        result["values"] = json.loads(result.pop("values_json"))
        yield result
