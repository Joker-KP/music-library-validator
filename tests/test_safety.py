import os
import subprocess
import sys

import pytest

from music_validator import cli
from music_validator.safety import exclusive_lock


@pytest.mark.parametrize(
    "kind", ["reports", "db", "report_symlink", "parent_symlink", "db_hardlink", "journal_symlink"]
)
def test_unsafe_outputs_rejected_without_source_changes(tmp_path, library, config_file, kind):
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    victim = next(library.rglob("*.mp3"))
    before = victim.read_bytes()
    if kind == "reports":
        reports = library
    elif kind == "db":
        db = library / "cache.sqlite"
    elif kind == "report_symlink":
        reports.mkdir()
        (reports / "index.html").symlink_to(victim)
    elif kind == "parent_symlink":
        (tmp_path / "linked").symlink_to(library, target_is_directory=True)
        db = tmp_path / "linked/cache.sqlite"
    elif kind == "db_hardlink":
        os.link(victim, db)
    elif kind == "journal_symlink":
        (tmp_path / "cache.sqlite-journal").symlink_to(victim)
    with pytest.raises((ValueError, OSError)):
        cli.run(config_file, db, reports)
    assert victim.read_bytes() == before


def test_other_process_cannot_take_scan_lock(tmp_path):
    lock = tmp_path / "cache.lock"
    with exclusive_lock(lock):
        code = "from pathlib import Path; from music_validator.safety import exclusive_lock; "
        code += f"\nwith exclusive_lock(Path({str(lock)!r})): pass\n"
        result = subprocess.run(
            [sys.executable, "-c", code], check=False, capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "already" in result.stderr
    with exclusive_lock(lock):
        pass


def test_output_dotdot_does_not_create_library_directories(tmp_path, library, config_file):
    db = library / "unused" / ".." / ".." / "cache.sqlite"
    cli.run(config_file, db, tmp_path / "reports")
    assert not (library / "unused").exists()


@pytest.mark.parametrize("locked", ["cache.sqlite.lock", "reports/.scan.lock"])
def test_cli_honors_both_locks(tmp_path, config_file, locked):
    with exclusive_lock(tmp_path / locked), pytest.raises(ValueError, match="already"):
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports")
