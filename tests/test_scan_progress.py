import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import signatures

from music_validator import cli, scanner


def test_count_only_uses_discovery_filters_and_never_opens_audio(library, tmp_path):
    (library / "cover.jpg").write_bytes(b"sidecar")
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "linked-directory").symlink_to(outside, target_is_directory=True)
    (library / "linked.mp3").symlink_to(next(library.rglob("*.mp3")))
    real_open = os.open
    increments = []

    def directory_only(path, flags, *args, **kwargs):
        assert Path(path).suffix.lower() not in (".mp3", ".flac")
        return real_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=directory_only):
        assert scanner.count_files(library, {".mp3", ".flac"}, increments.append) == 2
        assert scanner.count_files(library, {".mp3"}) == 1
    assert increments == [1, 1]


@pytest.mark.parametrize("estimated", [1, 3])
def test_changed_estimate_and_cached_tracks_report_actual_progress(
    tmp_path, library, config_file, capsys, estimated
):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    before = signatures(library)
    capsys.readouterr()
    with (
        patch.object(scanner, "count_files", return_value=estimated),
        patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")),
    ):
        cli.run(config_file, db, reports)
    output = capsys.readouterr()
    assert f"File count changed: counted {estimated}, processed 2" in output.err
    assert "100% 2/2 files" in output.err
    assert "reread 0" in output.out
    assert signatures(library) == before


def test_count_failure_preserves_previous_cache_and_reports(tmp_path, config_file, capsys):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    current = (reports / "current").readlink()
    capsys.readouterr()
    with (
        patch.object(scanner, "count_files", side_effect=PermissionError("count failed")),
        patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata read")),
        pytest.raises(PermissionError, match="count failed"),
    ):
        cli.run(config_file, db, reports)
    output = capsys.readouterr()
    assert "failed" in output.err and "Analyzing tracks" not in output.err
    assert (reports / "current").readlink() == current
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM tracks").fetchone()[0] == 2


def test_second_walk_failure_rolls_back_partial_processing(tmp_path, config_file, capsys):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    current = (reports / "current").readlink()
    capsys.readouterr()
    real_discover = scanner.discover
    walks = 0

    def discover(*args, **kwargs):
        nonlocal walks
        walks += 1
        for number, row in enumerate(real_discover(*args, **kwargs)):
            if walks == 2 and number == 1:
                raise PermissionError("analysis failed")
            yield row

    with patch.object(scanner, "discover", discover), pytest.raises(PermissionError):
        cli.run(config_file, db, reports, full=True)
    assert walks == 2 and (reports / "current").readlink() == current
    output = capsys.readouterr()
    assert "Analyzing tracks" in output.err and "failed" in output.err
    assert "100%" not in output.err
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM tracks").fetchone()[0] == 2


def test_no_progress_cli_suppresses_stderr_but_keeps_summary(tmp_path, config_file):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "music_validator",
            "scan",
            "--config",
            str(config_file),
            "--db",
            str(tmp_path / "cache.sqlite"),
            "--reports",
            str(tmp_path / "reports"),
            "--no-progress",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stderr == "" and "Indexed 2 tracks; reread 2" in result.stdout
    assert json.loads((tmp_path / "reports/report.json").read_text())["tracks"] == 2
