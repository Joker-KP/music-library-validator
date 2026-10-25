import csv
import json
import os
import shutil
import sqlite3
from unittest.mock import patch

import pytest
import yaml
from conftest import signatures
from test_audio_integration import FakeBackend
from test_integrity_integration import FakeIntegrity

from music_validator import cli, database, scanner
from music_validator import reports as rendering
from music_validator.integrity import CheckResult, finding, incomplete, selected_checks
from music_validator.safety import exclusive_lock


@pytest.fixture
def modes(tmp_path, config_file, monkeypatch):
    fingerprint, integrity = FakeBackend(), FakeIntegrity()
    monkeypatch.setattr(cli, "FingerprintBackend", lambda: fingerprint)

    def make_integrity(options, extensions):
        integrity.checks = {ext: selected_checks(options, ext) for ext in extensions}
        return integrity

    monkeypatch.setattr(cli, "IntegrityBackend", make_integrity)
    config = yaml.safe_load(config_file.read_text())
    config.update(
        {
            "audio_integrity": {"enabled": False},
            "audio_identity_rules": {
                "enabled": False,
                "fields": ["title"],
                "severity": "info",
                "matching": {"similarity_threshold": 0.85},
            },
        }
    )
    original = scanner.read_metadata

    def reader(path, **kwargs):
        metadata = original(path, **kwargs)
        metadata["tags"]["title"] = path.suffix + "< & >"
        return metadata

    monkeypatch.setattr(scanner, "read_metadata", reader)

    def run(mode="configured", **kwargs):
        config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        before = config_file.read_bytes()
        cli.run(
            config_file,
            tmp_path / "cache.sqlite",
            tmp_path / "reports",
            audio_integrity_only=mode == "integrity",
            audio_identity_only=mode == "identity",
            **kwargs,
        )
        assert config_file.read_bytes() == before
        dest = tmp_path / "reports"
        if mode != "configured":
            dest /= mode + "-report"
        return json.loads((dest / "report.json").read_text())

    return config, fingerprint, integrity, run


def assert_issue_formats(dest, report):
    with (dest / "issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == report["issues"]
    assert not (dest / "catalog.html").exists()


@pytest.mark.parametrize("mode", ["configured", "integrity", "identity"])
def test_directory_exclusions_skip_audio_and_local_yaml_in_every_mode(
    modes, library, tmp_path, mode, capsys
):
    config, audio, integrity, run = modes
    excluded = library / "Artist" / "#recycle" / "Album"
    excluded.mkdir(parents=True)
    for ext in ("mp3", "flac"):
        shutil.copyfile(next(library.rglob(f"*.{ext}")), excluded / f"01 - Hidden.{ext}")
    (excluded / "rules.yaml").write_text("invalid: [", encoding="utf-8")
    before = signatures(library)
    config["exclude_directory_patterns"] = [r"(^|/)#recycle(/|$)"]
    found = run(mode)
    assert found["tracks"] == 2
    assert "File count changed:" not in capsys.readouterr().err
    assert not any("#recycle" in i["path"] for i in found["issues"])
    assert audio.calls == (2 if mode == "identity" else 0)
    assert len(integrity.calls) == (3 if mode == "integrity" else 0)
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM files").fetchone()[0] == 2
        assert not any("#recycle" in path for (path,) in con.execute("SELECT path FROM files"))
    assert signatures(library) == before


def test_changed_exclusions_prune_all_caches_and_restore_files(modes, library, tmp_path):
    config, audio, integrity, run = modes
    config["audio_identity_rules"]["enabled"] = True
    config["audio_integrity"]["enabled"] = True
    excluded = next(library.rglob("*.mp3")).parent.parent / "#recycle"
    excluded.mkdir()
    for ext in ("mp3", "flac"):
        shutil.copyfile(next(library.rglob(f"*.{ext}")), excluded / f"01 - Hidden.{ext}")
    before = signatures(library)
    assert run()["tracks"] == 4
    calls = audio.calls, len(integrity.calls)
    config["exclude_directory_patterns"] = ["#recycle"]
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        found = run()
    assert found["tracks"] == 2
    assert (audio.calls, len(integrity.calls)) == calls
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        for table in ("files", "tracks", "audio_fingerprints", "audio_integrity"):
            paths = {path for (path,) in con.execute(f"SELECT path FROM {table}")}
            assert len(paths) == 2
            assert not any("#recycle" in path for path in paths)
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == found["issues"]
    assert "#recycle" not in (tmp_path / "reports/index.html").read_text()
    config["exclude_directory_patterns"] = []
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        assert run()["tracks"] == 4
    assert reader.call_count == 2
    assert (audio.calls, len(integrity.calls)) == (calls[0] + 2, calls[1] + 3)
    assert signatures(library) == before


@pytest.mark.parametrize("mode", ["integrity", "identity"])
def test_local_patches_are_validated_but_track_rules_are_skipped(modes, library, tmp_path, mode):
    _, _, _, run = modes
    local = library / "rules.yaml"
    local.write_text(
        yaml.safe_dump(
            {
                "added_required_tags": ["missing_local_tag"],
                "artwork": {"required": True},
                "added_custom_rules": [
                    {
                        "id": "local_rule",
                        "field": "comment",
                        "template": {"operator": "equals", "pattern": "never matches"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with patch.object(cli, "validate", side_effect=AssertionError("metadata rules")):
        found = run(mode)
    assert not any(
        i["rule"] in ("missing_missing_local_tag", "local_rule", "missing_artwork")
        for i in found["issues"]
    )
    current = (tmp_path / "reports" / f"{mode}-report/current").readlink()
    local.write_text("artwork: null", encoding="utf-8")
    with pytest.raises(ValueError, match="rules.yaml"):
        run(mode)
    assert (tmp_path / "reports" / f"{mode}-report/current").readlink() == current


def test_integrity_only_without_metadata_or_unrelated_work(
    modes, library, tmp_path, monkeypatch, capsys
):
    _, audio, integrity, run = modes
    before = signatures(library)
    original_discover = scanner.discover
    walks = []

    def discover(*args, **kwargs):
        walks.append(True)
        yield from original_discover(*args, **kwargs)

    monkeypatch.setattr(scanner, "discover", discover)
    with (
        patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata")),
        patch.object(cli, "FingerprintBackend", side_effect=AssertionError("fingerprint")),
        patch.object(cli, "validate", side_effect=AssertionError("tag rules")),
        patch.object(cli, "summarize_album", side_effect=AssertionError("catalog")),
        patch.object(cli, "validate_cross_album", side_effect=AssertionError("cross-album")),
    ):
        report = run("integrity")
        assert len(walks) == 2 and len(integrity.calls) == 3 and not audio.calls
        assert report["tracks"] == 2 and not report["issues"]
        assert len(report["results"]) == 3
        assert all(row["source"] == "executed" and row["checked_at"] for row in report["results"])
        assert len({row["album"] for row in report["results"]}) == 1
        assert run("integrity")["analysis"]["statistics"]["checks_reused"] == 3
        run("integrity", recheck_integrity=True)
        run("integrity", full=True)
    assert len(integrity.calls) == 9
    assert signatures(library) == before
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM files").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM tracks").fetchone()[0] == 0
        assert con.execute("SELECT DISTINCT mode FROM scans").fetchall() == [("integrity",)]
    assert not (tmp_path / "reports/index.html").exists()
    assert_issue_formats(tmp_path / "reports/integrity-report", report)
    output = capsys.readouterr()
    assert "100% 2/2 files" in output.err and "Validating tags" not in output.err
    assert "Catalog:" not in output.out


def test_independent_reports_and_shared_cache(modes, library, tmp_path):
    config, audio, integrity, run = modes
    before = signatures(library)
    run()
    normal = tmp_path / "reports"
    snapshot = {
        name: (normal / name).read_bytes()
        for name in ("index.html", "issues.csv", "report.json", "catalog.json")
    }
    normal_current = (normal / "current").readlink()
    with (
        patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")),
        patch.object(cli, "validate", side_effect=AssertionError("tag rules")),
        patch.object(cli, "summarize_album", side_effect=AssertionError("catalog")),
    ):
        report = run("identity")
        assert len(report["issues"]) == 2
        assert all(
            item["rule"] == "audio_identity_title" and item["severity"] == "info"
            for item in report["issues"]
        )
        assert report["analysis"]["settings"]["matching"]["similarity_threshold"] == 0.85
        stats = report["analysis"]["statistics"]
        assert stats["matched_groups"] == stats["groups_with_conflicts"] == 1
        assert stats["tracks_by_status"]["matched"] == 2
        assert len(report["groups"][0]["tracks"]) == 2
        assert report["groups"][0]["conflicting_fields"] == ["title"]
        document = (normal / "identity-report/index.html").read_text()
        assert "&lt; &amp; &gt;" in document and ".mp3< & >" not in document
        assert audio.calls == 2 and not integrity.calls
        assert run("identity")["analysis"]["statistics"]["fingerprints_reused"] == 2
        run("identity", recheck_identity=True)
        assert audio.calls == 4
        config["audio_identity_rules"]["fields"] = ["composer"]
        report = run("identity")
        assert report["issues"] == [] and len(report["groups"]) == 1
        assert report["groups"][0]["conflicting_fields"] == []
        assert audio.calls == 4
        identity_snapshot = (normal / "identity-report/report.json").read_bytes()
        run("integrity")
        assert (normal / "identity-report/report.json").read_bytes() == identity_snapshot
    assert (normal / "current").readlink() == normal_current
    assert all((normal / name).read_bytes() == content for name, content in snapshot.items())
    assert signatures(library) == before
    assert_issue_formats(normal / "identity-report", report)
    html = (normal / "identity-report/index.html").read_text()
    assert "Audio identity report" in html and "Scope and coverage" in html
    assert "Matched audio groups" in html
    config["audio_identity_rules"]["enabled"] = True
    config["audio_integrity"]["enabled"] = True
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        run()
    assert audio.calls == 4 and len(integrity.calls) == 3


def test_integrity_inventory_does_not_mark_stale_metadata_current(modes, library, tmp_path):
    _, _, _, run = modes
    run()
    mp3 = next(library.rglob("*.mp3"))
    stat = mp3.stat()
    os.utime(mp3, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    with patch.object(
        scanner, "read_metadata", side_effect=AssertionError("metadata in integrity mode")
    ):
        run("integrity")
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute(
            "SELECT size,mtime_ns FROM tracks WHERE path=?", (str(mp3),)
        ).fetchone() == (stat.st_size, stat.st_mtime_ns)
        assert (
            con.execute("SELECT mtime_ns FROM files WHERE path=?", (str(mp3),)).fetchone()[0]
            != stat.st_mtime_ns
        )
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run()
    assert reader.call_count == 1


def test_integrity_first_then_identity_then_normal(modes, tmp_path):
    _, audio, integrity, run = modes
    run("integrity")
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run("identity")
    assert reader.call_count == 2 and audio.calls == 2 and len(integrity.calls) == 3
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        run()
    assert (tmp_path / "reports/catalog.json").exists()


def test_identity_respects_current_cached_integrity_errors(modes, library):
    _, audio, integrity, run = modes
    integrity.results["ffmpeg"] = CheckResult(
        "failed", [finding("audio_decode_error", "bad frame", "error")]
    )
    run("integrity")
    with patch.object(cli, "IntegrityBackend", side_effect=AssertionError("integrity started")):
        report = run("identity")
    assert audio.calls == 1
    assert [item["rule"] for item in report["issues"]] == ["audio_identity_skipped_integrity"]
    assert report["analysis"]["statistics"]["tracks_by_status"]["skipped_integrity"] == 1
    assert not report["groups"]
    assert any(row["status"] == "skipped_integrity" and row["reason"] for row in report["results"])
    mp3 = next(library.rglob("*.mp3"))
    stat = mp3.stat()
    os.utime(mp3, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    assert len(run("identity")["issues"]) == 2  # Stale integrity failure is ignored.
    assert audio.calls == 2


def test_failures_and_no_applicable_checks_have_visible_coverage(modes):
    config, audio, integrity, run = modes
    config["audio_integrity"]["mp3"] = {"structure_check": False, "decode_check": False}
    integrity.results["flac"] = incomplete("flac", "timeout")
    report = run("integrity")
    assert report["analysis"]["statistics"]["files_without_applicable_check"] == 1
    assert {row["status"] for row in report["results"]} == {"not_checked", "incomplete"}
    run("integrity")
    assert integrity.calls == ["flac", "flac"]
    audio.fail = True
    report = run("identity")
    assert report["analysis"]["statistics"]["tracks_by_status"]["fingerprint_error"] == 2
    assert all(row["reason"] for row in report["results"])
    assert report["analysis"]["statistics"]["fingerprint_attempts"] == 2
    assert report["analysis"]["statistics"]["fingerprints_extracted"] == 0
    assert {row["rule"] for row in report["issues"]} == {"audio_fingerprint_error"}


def test_full_scan_identity_does_not_run_integrity(modes):
    _, audio, integrity, run = modes
    run("identity")
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run("identity", full=True)
    assert reader.call_count == 2 and audio.calls == 4 and not integrity.calls


def test_identity_metadata_failures_are_reported_without_tag_rules(modes):
    _, audio, _, run = modes
    with patch.object(scanner, "read_metadata", side_effect=ValueError("unreadable metadata")):
        report = run("identity")
    assert audio.calls == 2 and not report["groups"]
    assert {item["rule"] for item in report["issues"]} == {"read_error"}
    assert report["analysis"]["statistics"]["tracks_by_status"]["metadata_error"] == 2
    assert all(row["reason"] for row in report["results"])


def test_integrity_only_prunes_removed_files_without_metadata(modes, library, tmp_path):
    _, _, _, run = modes
    run("identity")
    run("integrity")
    next(library.rglob("*.mp3")).unlink()  # Remove a temporary synthetic fixture.
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        report = run("integrity")
    assert report["tracks"] == 1
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        for table in ("files", "tracks", "audio_integrity", "audio_fingerprints"):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_recheck_identity_in_normal_mode_retains_current_metadata(modes):
    config, audio, _, run = modes
    config["audio_identity_rules"]["enabled"] = True
    run()
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        run(recheck_identity=True)
    assert audio.calls == 4


@pytest.mark.parametrize("mode", ["integrity", "identity"])
def test_specialist_publication_failure_keeps_previous_generation(modes, tmp_path, mode):
    _, _, _, run = modes
    run(mode)
    dest = tmp_path / "reports" / (mode + "-report")
    current = (dest / "current").readlink()
    before = {
        name: (dest / name).read_bytes() for name in ("index.html", "issues.csv", "report.json")
    }
    with (
        patch.object(rendering, "_switch_current", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        run(mode)
    assert (dest / "current").readlink() == current
    assert all((dest / name).read_bytes() == content for name, content in before.items())
    assert len(list((dest / "generations").iterdir())) == 1


def test_mode_failure_rolls_back_inventory_and_cache(modes, library, tmp_path):
    _, audio, _, run = modes
    run("identity")
    audio.version = "updated"
    extra = next(library.rglob("*.mp3")).with_name("02 - Extra.mp3")
    shutil.copyfile(next(library.rglob("*.mp3")), extra)
    with (
        patch.object(cli, "validate_audio_identity", side_effect=RuntimeError("matching failed")),
        pytest.raises(RuntimeError),
    ):
        run("identity")
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM files").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert con.execute(
            "SELECT DISTINCT backend_version FROM audio_fingerprints"
        ).fetchall() == [("fake-full-track-v1",)]


@pytest.mark.parametrize(
    "options,config_override",
    [
        ({"audio_integrity_only": True, "audio_identity_only": True}, {}),
        (
            {"audio_integrity_only": True},
            {
                "audio_integrity": {
                    "enabled": False,
                    "flac": {"native_test": False},
                    "mp3": {"structure_check": False, "decode_check": False},
                }
            },
        ),
        ({"audio_identity_only": True}, {"audio_identity_rules": {"fields": []}}),
        ({"audio_identity_only": True, "recheck_integrity": True}, {}),
        ({"audio_integrity_only": True, "recheck_identity": True}, {}),
        ({"recheck_identity": True}, {}),
    ],
)
def test_invalid_mode_options_before_outputs(tmp_path, config_file, options, config_override):
    config = yaml.safe_load(config_file.read_text())
    config.update(config_override)
    config_file.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports", **options)
    assert not (tmp_path / "cache.sqlite").exists() and not (tmp_path / "reports").exists()


def test_cli_mutual_exclusion():
    with pytest.raises(SystemExit) as exc:
        cli.main(["scan", "--audio-integrity-only", "--audio-identity-only"])
    assert exc.value.code == 2


def test_shared_database_lock_prevents_cross_mode_overlap(modes, tmp_path):
    _, _, _, run = modes
    with (
        exclusive_lock(tmp_path / "cache.sqlite.lock"),
        pytest.raises(ValueError, match="already using"),
    ):
        run("integrity")
    assert not (tmp_path / "cache.sqlite").exists()


def test_profile_output_symlink_is_rejected(modes, tmp_path):
    _, _, _, run = modes
    dest = tmp_path / "reports"
    dest.mkdir()
    (dest / "integrity-report").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        run("integrity")
    assert not (tmp_path / "outside").exists()


def test_schema3_migration_preserves_all_analysis_caches(modes, tmp_path):
    _, _, _, run = modes
    run("identity")
    run("integrity")
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as con:
        before = {
            table: con.execute(f"SELECT * FROM {table} ORDER BY path").fetchall()
            for table in ("tracks", "audio_integrity", "audio_fingerprints")
        }
        con.execute("DROP TABLE files")
        con.execute("ALTER TABLE scans DROP COLUMN mode")
        con.execute("PRAGMA user_version=3")
    con = database.connect(path)
    con.rollback()
    con.close()
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    con = database.connect(path)
    try:
        assert con.execute("SELECT count(*) FROM files").fetchone()[0] == 2
        for table, rows in before.items():
            assert [
                tuple(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY path")
            ] == rows
        assert con.execute("SELECT DISTINCT mode FROM scans").fetchall()[0][0] == "configured"
    finally:
        con.rollback()
        con.close()
