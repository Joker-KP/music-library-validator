"""Versioned SQLite cache; one explicit transaction owns a complete scan."""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4
PARSER_VERSION = 6


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
        if version < 3:
            conn.execute("""CREATE TABLE audio_integrity (
                path TEXT NOT NULL, check_name TEXT NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                backend_version TEXT NOT NULL, result_json TEXT NOT NULL,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(path, check_name)
            )""")
        if version < 4:
            conn.execute("""CREATE TABLE files (
                path TEXT PRIMARY KEY, album_dir TEXT NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                last_seen_scan INTEGER NOT NULL
            )""")
            conn.execute("""INSERT INTO files SELECT path,album_dir,size,mtime_ns,last_seen_scan
                FROM tracks""")
            conn.execute("CREATE INDEX idx_files_album ON files(album_dir,path)")
            conn.execute("ALTER TABLE scans ADD COLUMN mode TEXT NOT NULL DEFAULT 'configured'")
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
        for row in conn.execute("SELECT path FROM files"):
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
    for row in conn.execute("""SELECT tracks.* FROM tracks JOIN files USING(path)
        WHERE tracks.size=files.size AND tracks.mtime_ns=files.mtime_ns
        ORDER BY tracks.album_dir,tracks.path"""):
        yield dict(row)


def record_file(conn, path, album, info, scan_id):
    conn.execute(
        """INSERT INTO files VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
        album_dir=excluded.album_dir,size=excluded.size,mtime_ns=excluded.mtime_ns,
        last_seen_scan=excluded.last_seen_scan""",
        (str(path), str(album), info.st_size, info.st_mtime_ns, scan_id),
    )


def prune_files(conn, scan_id):
    conn.execute("DELETE FROM files WHERE last_seen_scan<>?", (scan_id,))
    conn.execute(
        "DELETE FROM tracks WHERE NOT EXISTS (SELECT 1 FROM files WHERE files.path=tracks.path)"
    )
    prune_audio_fingerprints(conn)
    prune_integrity_results(conn)


def cached_integrity_errors(conn, path, info):
    for row in conn.execute(
        "SELECT result_json FROM audio_integrity WHERE path=? AND size=? AND mtime_ns=?",
        (str(path), info.st_size, info.st_mtime_ns),
    ):
        if any(item["severity"] == "error" for item in json.loads(row[0])["findings"]):
            return True
    return False


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
        SELECT 1 FROM files WHERE files.path=audio_fingerprints.path
        AND files.size=audio_fingerprints.size AND files.mtime_ns=audio_fingerprints.mtime_ns
    )""")


def iter_audio_rows(conn):
    for row in conn.execute("""SELECT tracks.*,audio_fingerprints.duration,
        audio_fingerprints.fingerprint_json,audio_fingerprints.error AS fingerprint_error
        FROM files JOIN tracks USING(path) JOIN audio_fingerprints USING(path)
        WHERE tracks.size=files.size AND tracks.mtime_ns=files.mtime_ns
        AND audio_fingerprints.size=files.size AND audio_fingerprints.mtime_ns=files.mtime_ns
        ORDER BY tracks.path"""):
        yield dict(row)


def integrity_result(conn, path, check):
    return conn.execute(
        "SELECT * FROM audio_integrity WHERE path=? AND check_name=?", (str(path), check)
    ).fetchone()


def save_integrity_result(conn, path, check, info, version, result):
    conn.execute(
        """INSERT INTO audio_integrity
        (path,check_name,size,mtime_ns,backend_version,result_json) VALUES(?,?,?,?,?,?)
        ON CONFLICT(path,check_name) DO UPDATE SET size=excluded.size,
        mtime_ns=excluded.mtime_ns,backend_version=excluded.backend_version,
        result_json=excluded.result_json,checked_at=CURRENT_TIMESTAMP""",
        (
            str(path),
            check,
            info.st_size,
            info.st_mtime_ns,
            version,
            json.dumps(result.as_dict(), ensure_ascii=False),
        ),
    )


def prune_integrity_results(conn):
    conn.execute("""DELETE FROM audio_integrity WHERE NOT EXISTS (
        SELECT 1 FROM files WHERE files.path=audio_integrity.path
        AND files.size=audio_integrity.size AND files.mtime_ns=audio_integrity.mtime_ns
    )""")


def iter_integrity_rows(conn):
    for row in conn.execute("""SELECT files.path,files.album_dir,
        audio_integrity.check_name,audio_integrity.result_json
        FROM files JOIN audio_integrity USING(path)
        WHERE audio_integrity.size=files.size AND audio_integrity.mtime_ns=files.mtime_ns
        ORDER BY files.album_dir,files.path,audio_integrity.check_name"""):
        yield dict(row)


def start_audio_activity(conn):
    conn.execute("""CREATE TEMP TABLE integrity_activity (
        path TEXT NOT NULL, check_name TEXT NOT NULL, source TEXT NOT NULL,
        PRIMARY KEY(path,check_name))""")
    conn.execute("""CREATE TEMP TABLE identity_activity (
        path TEXT PRIMARY KEY, status TEXT NOT NULL, source TEXT NOT NULL,
        reason TEXT NOT NULL)""")
    conn.execute("CREATE TEMP TABLE identity_groups (details_json TEXT NOT NULL)")


def iter_integrity_activity(conn):
    for row in conn.execute("""SELECT files.path, files.album_dir AS album,
        activity.check_name AS tool,activity.source,cache.checked_at,cache.result_json
        FROM integrity_activity activity JOIN files USING(path)
        LEFT JOIN audio_integrity cache
        ON cache.path=activity.path AND cache.check_name=activity.check_name
        ORDER BY files.album_dir,files.path,activity.check_name"""):
        result = dict(row)
        checked = json.loads(result.pop("result_json") or '{"status":"not_checked","findings":[]}')
        yield {**result, **checked}


def iter_identity_activity(conn):
    for row in conn.execute("""SELECT files.path,files.album_dir AS album,
        activity.status,activity.source,activity.reason
        FROM identity_activity activity JOIN files USING(path)
        ORDER BY files.album_dir,files.path"""):
        yield dict(row)


def save_identity_group(conn, group):
    conn.execute("INSERT INTO identity_groups VALUES(?)", (json.dumps(group, ensure_ascii=False),))
    conn.executemany(
        "UPDATE identity_activity SET status='matched' WHERE path=?",
        ((row["path"],) for row in group["tracks"]),
    )


def iter_identity_groups(conn):
    for row in conn.execute("SELECT details_json FROM identity_groups ORDER BY rowid"):
        yield json.loads(row[0])


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
