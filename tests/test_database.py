import sqlite3
from unittest.mock import patch

import pytest
import yaml

from music_validator import cli, database, scanner


def test_legacy_cache_migrates_without_discarding_rows(tmp_path, library):
    path = tmp_path / "legacy.sqlite"
    track = next(library.rglob("*.mp3"))
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE tracks (path TEXT PRIMARY KEY, album_dir TEXT NOT NULL, "
            "size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, metadata_json TEXT NOT NULL, "
            "error TEXT, scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        con.execute(
            "INSERT INTO tracks(path,album_dir,size,mtime_ns,metadata_json) VALUES(?,?,?,?,?)",
            (str(track), str(track.parent), track.stat().st_size, track.stat().st_mtime_ns, "{}"),
        )
    con = database.connect(path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert con.execute("SELECT path FROM tracks").fetchone()[0] == str(track)
        assert con.execute("SELECT parser_version FROM tracks").fetchone()[0] == 0
        con.commit()
    finally:
        con.close()


def test_library_scope_change_refused_without_pruning(tmp_path, library, config_file):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    other = tmp_path / "other-music"
    other.mkdir()
    config_file.write_text(yaml.safe_dump({"library_root": str(other)}))
    with pytest.raises(ValueError, match="different|scope"):
        cli.run(config_file, db, reports)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM tracks").fetchone()[0] == 2


def test_extension_scope_change_refused(tmp_path, config_file):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    config = yaml.safe_load(config_file.read_text())
    config["extensions"] = [".mp3"]
    config_file.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="scope|extensions"):
        cli.run(config_file, db, reports)


def test_parser_version_upgrade_rereads(tmp_path, config_file):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE tracks SET parser_version=0")
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        cli.run(config_file, db, reports)
    assert reader.call_count == 2
    with sqlite3.connect(db) as con:
        assert (
            con.execute("SELECT count(*) FROM scans WHERE finished_at IS NOT NULL").fetchone()[0]
            == 2
        )


def test_future_schema_refused(tmp_path):
    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="schema"):
        database.connect(path)
