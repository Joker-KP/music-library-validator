import csv
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from conftest import signatures

from music_validator import cli, database, scanner
from music_validator.config import DEFAULTS
from music_validator.integrity import (
    CheckResult,
    IntegrityBackend,
    finding,
    incomplete,
    selected_checks,
)


class FakeIntegrity:
    def __init__(self):
        self.checks = {".flac": ("flac",), ".mp3": ("mp3val", "ffmpeg")}
        self.versions = dict.fromkeys(("flac", "mp3val", "ffmpeg"), "fake-integrity-v1")
        self.calls = []
        self.results = {check: CheckResult("passed") for check in self.versions}

    def check(self, check, source):
        assert fcntl.fcntl(source.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        self.calls.append(check)
        return self.results[check]


@pytest.fixture
def setup_integrity(tmp_path, config_file, monkeypatch):
    backend = FakeIntegrity()
    config = yaml.safe_load(config_file.read_text())
    config.update(
        {
            "required_tags": [],
            "artwork": {"required": False},
            "filename": {"regex": None, "allowed_chars": None},
            "cross_album_rules": {"enabled": False},
            "audio_integrity": {"enabled": True},
        }
    )

    def make_backend(options, extensions):
        backend.checks = {ext: selected_checks(options, ext) for ext in extensions}
        return backend

    monkeypatch.setattr(cli, "IntegrityBackend", make_backend)

    def scan(**kwargs):
        config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports", **kwargs)
        return json.loads((tmp_path / "reports/report.json").read_text())["issues"]

    return backend, config, scan


def test_cache_reports_and_recheck_without_metadata_reads(
    setup_integrity, library, tmp_path, monkeypatch
):
    backend, config, scan = setup_integrity
    backend.results["mp3val"] = CheckResult(
        "warning", [finding("mp3_structure_warning", "<bad & frame>: possible truncation or junk")]
    )
    backend.results["flac"] = CheckResult(
        "failed", [finding("flac_checksum_mismatch", "MD5 mismatch", "error")]
    )
    before = signatures(library)
    original_discover = scanner.discover
    walks = []

    def discover(*args, **kwargs):
        walks.append(True)
        yield from original_discover(*args, **kwargs)

    monkeypatch.setattr(scanner, "discover", discover)
    found = scan()
    assert len(walks) == 2
    assert len(backend.calls) == 3 and len(found) == 2
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as source:
        assert list(csv.DictReader(source)) == found
    html = (tmp_path / "reports/index.html").read_text()
    assert "&lt;bad &amp; frame&gt;" in html and "<bad & frame>" not in html
    catalog = json.loads((tmp_path / "reports/catalog.json").read_text())["albums"]
    assert len(catalog) == 1 and catalog[0]["number_of_issues"] == 2
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert scan() == found and len(backend.calls) == 3
        config["audio_integrity"]["timeout_seconds"] = 600
        assert scan() == found and len(backend.calls) == 3
        config["audio_integrity"]["enabled"] = False
        assert scan() == [] and len(backend.calls) == 3
        config["audio_integrity"]["enabled"] = True
        assert scan() == found and len(backend.calls) == 3
        assert scan(recheck_integrity=True) == found and len(backend.calls) == 6
        backend.versions["flac"] = "new-version"
        assert scan() == found and len(backend.calls) == 7
    scan(full=True)
    assert len(backend.calls) == 10
    assert signatures(library) == before


def test_enabling_new_checks_only_runs_missing_checks(setup_integrity):
    backend, config, scan = setup_integrity
    config["audio_integrity"]["enabled"] = False
    assert scan() == [] and not backend.calls
    config["audio_integrity"].update(
        enabled=True, mp3={"structure_check": False}, flac={"native_test": False}
    )
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert scan() == [] and backend.calls == ["ffmpeg"]
        config["audio_integrity"]["mp3"]["structure_check"] = True
        assert scan() == [] and backend.calls == ["ffmpeg", "mp3val"]
        config["audio_integrity"]["flac"]["native_test"] = True
        assert scan() == [] and backend.calls == ["ffmpeg", "mp3val", "flac"]


def test_incomplete_checks_retry_and_preserve_metadata(setup_integrity, tmp_path):
    backend, _, scan = setup_integrity
    backend.results["ffmpeg"] = incomplete("ffmpeg", "timed out")
    assert [item["rule"] for item in scan()] == ["audio_integrity_incomplete"]
    assert len(backend.calls) == 3
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM tracks WHERE error IS NULL").fetchone()[0] == 2
    backend.results["ffmpeg"] = CheckResult("passed")
    assert scan() == [] and backend.calls.count("ffmpeg") == 2 and len(backend.calls) == 4


def test_failed_integrity_excludes_new_and_cached_fingerprints(setup_integrity, monkeypatch):
    from test_audio_integration import FakeBackend

    integrity, config, scan = setup_integrity
    audio = FakeBackend()
    config["audio_identity_rules"] = {"enabled": True}
    monkeypatch.setattr(cli, "FingerprintBackend", lambda: audio)
    original_reader = scanner.read_metadata

    def reader(path, **kwargs):
        metadata = original_reader(path, **kwargs)
        metadata["tags"]["title"] = path.suffix
        return metadata

    monkeypatch.setattr(scanner, "read_metadata", reader)
    assert len(scan()) == 2 and audio.calls == 2
    integrity.results["ffmpeg"] = CheckResult(
        "failed", [finding("audio_decode_error", "Header missing", "error")]
    )
    found = scan(recheck_integrity=True)
    assert [item["rule"] for item in found] == ["audio_decode_error"] and audio.calls == 2
    scan(full=True)
    assert audio.calls == 3  # Only the FLAC is fingerprinted.
    integrity.results["ffmpeg"] = CheckResult("passed")
    assert len(scan(recheck_integrity=True)) == 2
    assert audio.calls == 3  # Original valid MP3 fingerprint still available.
    integrity.results["mp3val"] = CheckResult(
        "warning", [finding("mp3_structure_warning", "possible truncation or trailing junk")]
    )
    assert len(scan(recheck_integrity=True)) == 3 and audio.calls == 3


def test_changed_files_and_disabled_pruning(setup_integrity, library, tmp_path):
    backend, config, scan = setup_integrity
    scan()
    mp3 = next(library.rglob("*.mp3"))
    info = mp3.stat()
    # Change a synthetic fixture's signature, never user media.
    os.utime(mp3, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
    scan()
    assert len(backend.calls) == 5
    config["audio_integrity"]["enabled"] = False
    mp3.unlink()
    scan()
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT check_name FROM audio_integrity").fetchall() == [("flac",)]


def test_migration_preserves_both_caches_and_rolls_back(setup_integrity, tmp_path):
    _, config, scan = setup_integrity
    config["audio_integrity"]["enabled"] = False
    scan()
    db = tmp_path / "cache.sqlite"
    with sqlite3.connect(db) as con:
        metadata = con.execute("SELECT * FROM tracks ORDER BY path").fetchall()
        con.execute("INSERT INTO audio_fingerprints VALUES('sentinel',1,2,'old',3,'[4]',NULL)")
        con.execute("DROP TABLE audio_integrity")
        con.execute("DROP TABLE files")
        con.execute("ALTER TABLE scans DROP COLUMN mode")
        con.execute("PRAGMA user_version=2")
    con = database.connect(db)
    con.rollback()
    con.close()
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            con.execute("SELECT name FROM sqlite_master WHERE name='audio_integrity'").fetchone()
            is None
        )
    con = database.connect(db)
    try:
        assert [tuple(row) for row in con.execute("SELECT * FROM tracks ORDER BY path")] == metadata
        assert con.execute("SELECT path FROM audio_fingerprints").fetchone()[0] == "sentinel"
        con.commit()
        assert con.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    finally:
        con.close()


def test_failed_scan_rolls_back_integrity_and_reports(setup_integrity, tmp_path):
    backend, _, scan = setup_integrity
    scan()
    current = (tmp_path / "reports/current").readlink()
    backend.versions["flac"] = "new-version"
    with (
        patch.object(cli, "validate", side_effect=RuntimeError("failed validation")),
        pytest.raises(RuntimeError),
    ):
        scan()
    assert (tmp_path / "reports/current").readlink() == current
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert con.execute("SELECT DISTINCT backend_version FROM audio_integrity").fetchall() == [
            ("fake-integrity-v1",)
        ]


def test_changed_during_check_is_incomplete(setup_integrity, monkeypatch):
    backend, _, scan = setup_integrity
    original = backend.check

    def changed(check, source):
        original(check, source)
        # Simulate an external actor changing a synthetic fixture during a check.
        info = os.fstat(source.fileno())
        os.utime(source.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        return CheckResult("passed")

    monkeypatch.setattr(backend, "check", changed)
    assert {item["rule"] for item in scan()} == {"audio_integrity_incomplete"}
    assert len(backend.calls) == 2  # Second MP3 check rejects the stale discovery signature.


@pytest.mark.skipif(
    any(not shutil.which(tool) for tool in ("flac", "mp3val", "ffmpeg")),
    reason="flac, mp3val and FFmpeg required",
)
def test_real_invalid_empty_and_short_silent_audio(tmp_path):
    options = deepcopy(DEFAULTS["audio_integrity"])
    options["enabled"] = True
    backend = IntegrityBackend(options, {".flac", ".mp3"})
    for extension in (".flac", ".mp3"):
        for index, content in enumerate((b"not audio data", b"")):
            path = tmp_path / f"bad-{index}{extension}"
            path.write_bytes(content)
            for check in backend.checks[extension]:
                with path.open("rb") as source:
                    assert backend.check(check, source).status == "failed"
        fixture = Path(__file__).parent / "fixtures" / f"silence{extension}"
        for check in backend.checks[extension]:
            with fixture.open("rb") as source:
                checked = backend.check(check, source)
                assert checked.status in ("passed", "warning")
                assert not any(item["severity"] == "error" for item in checked.findings)


@pytest.mark.skipif(
    any(not shutil.which(tool) for tool in ("flac", "mp3val", "ffmpeg")),
    reason="flac, mp3val and FFmpeg required",
)
def test_real_tools_valid_damaged_and_checksumless_fixtures(tmp_path, capsys):
    root = tmp_path / "music"
    album = root / "Żółć"
    album.mkdir(parents=True)
    for extension in ("mp3", "flac"):
        path = album / f"01 - Good.{extension}"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-metadata",
                "title=Synthetic",
                str(path),
            ],
            capture_output=True,
            check=True,
        )
        content = path.read_bytes()
        (album / f"02 - Truncated.{extension}").write_bytes(content[:-200])
        if extension == "flac":
            assert content[:8] == b"fLaC\x00\x00\x00\x22"
            (album / "03 - Missing MD5.flac").write_bytes(content[:26] + bytes(16) + content[42:])
            (album / "04 - Wrong MD5.flac").write_bytes(
                content[:26] + bytes([content[26] ^ 1]) + content[27:]
            )
        else:
            (album / "03 - Junk.mp3").write_bytes(content + b"junk junk junk" * 100)
    before = signatures(root)
    options = deepcopy(DEFAULTS["audio_integrity"])
    options["enabled"] = True
    backend = IntegrityBackend(options, {".flac", ".mp3"})
    for extension in ("mp3", "flac"):
        for check in backend.checks["." + extension]:
            with (album / f"01 - Good.{extension}").open("rb") as source:
                assert backend.check(check, source).status == "passed"
    config = tmp_path / "rules.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "library_root": str(root),
                "audio_integrity": options,
                "required_tags": [],
                "artwork": {"required": False},
                "filename": {"regex": None, "allowed_chars": None},
                "cross_album_rules": {"enabled": False},
                "album_rules": {"unique_disc_track": False},
            }
        )
    )
    arguments = [
        "scan",
        "--config",
        str(config),
        "--db",
        str(tmp_path / "cache.sqlite"),
        "--reports",
        str(tmp_path / "reports"),
        "--no-progress",
    ]
    assert cli.main(arguments) == 0
    found = json.loads((tmp_path / "reports/report.json").read_text())["issues"]
    rules = {item["rule"] for item in found}
    assert {
        "audio_decode_error",
        "mp3_structure_warning",
        "flac_checksum_unavailable",
        "flac_checksum_mismatch",
    } <= rules
    assert "audio_integrity_incomplete" not in rules
    assert not any("01 - Good" in item["path"] for item in found)
    assert "ran 10 checks" in capsys.readouterr().out
    with patch.object(IntegrityBackend, "check", side_effect=AssertionError("integrity reread")):
        assert cli.main(arguments) == 0
    assert "ran 0 checks" in capsys.readouterr().out
    assert cli.main(arguments + ["--recheck-integrity"]) == 0
    assert "ran 10 checks" in capsys.readouterr().out
    assert signatures(root) == before
