import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
import yaml
from conftest import signatures
from test_audio_integration import FakeBackend
from test_integrity_integration import FakeIntegrity

from music_validator import cli, maintenance
from music_validator.integrity import selected_checks
from music_validator.jobs import BusyError, JobManager
from music_validator.report_store import ReportStore
from music_validator.safety import LockBusyError, exclusive_lock


def scan(config_file, tmp_path, **kwargs):
    cli.run(
        config_file, tmp_path / "cache.sqlite", tmp_path / "reports", show_progress=False, **kwargs
    )


def report_contents(root):
    contents = {}
    for path in root.rglob("*"):
        if path.name == ".scan.lock":
            continue
        if path.is_symlink():
            contents[str(path.relative_to(root))] = ("link", str(path.readlink()))
        elif path.is_file():
            contents[str(path.relative_to(root))] = ("file", path.read_bytes())
    return contents


@pytest.fixture
def outputs(config_file, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FingerprintBackend", FakeBackend)

    def integrity(options, extensions):
        backend = FakeIntegrity()
        backend.checks = {ext: selected_checks(options, ext) for ext in extensions}
        return backend

    monkeypatch.setattr(cli, "IntegrityBackend", integrity)
    scan(config_file, tmp_path)
    scan(config_file, tmp_path, audio_integrity_only=True)
    scan(config_file, tmp_path, audio_identity_only=True)
    return tmp_path / "cache.sqlite", tmp_path / "reports"


def test_archive_all_managed_state_preserves_sources_archives_and_lock_inodes(
    outputs, config_file, library, tmp_path
):
    db, reports = outputs
    before = signatures(library)
    original_reports = report_contents(reports)
    for suffix in ("-journal", "-wal", "-shm"):
        Path(str(db) + suffix).write_bytes(b"sidecar fixture")
    original_cache = {
        p.name: p.read_bytes()
        for p in tmp_path.glob("cache.sqlite*")
        if not p.name.endswith(".lock")
    }
    old_data = tmp_path / "previous-data-existing"
    old_reports = reports / "previous-reports-existing"
    for path in (old_data, old_reports):
        path.mkdir()
        (path / "keep.txt").write_text("keep previous archive")
    (tmp_path / "notes.txt").write_text("unrelated data")
    (reports / "notes.txt").write_text("unrelated reports")
    lock_paths = [
        Path(str(db) + ".lock"),
        reports / ".scan.lock",
        reports / "integrity-report/.scan.lock",
        reports / "identity-report/.scan.lock",
    ]
    inodes = {p: p.stat().st_ino for p in lock_paths}
    store = ReportStore(reports)
    assert all(summary["available"] for summary in store.summaries().values())
    manager = JobManager(config_file, db, reports)
    receipt = manager.fresh_start(store)
    data_archive, report_archive = (
        Path(receipt[key]) for key in ("data_archive", "reports_archive")
    )
    assert data_archive.parent == db.parent and report_archive.parent == reports
    assert data_archive.name.startswith("previous-data-")
    assert report_archive.name.startswith("previous-reports-")
    assert {p.name: p.read_bytes() for p in data_archive.iterdir()} == original_cache
    assert report_contents(report_archive) == original_reports
    assert {p: p.stat().st_ino for p in lock_paths} == inodes
    assert not db.exists()
    assert all(not summary["available"] for summary in store.summaries().values())
    assert not store.cache and manager.snapshot()["reset"] == receipt
    assert (tmp_path / "notes.txt").read_text() == "unrelated data"
    assert (reports / "notes.txt").read_text() == "unrelated reports"
    assert (old_data / "keep.txt").exists() and (old_reports / "keep.txt").exists()
    assert signatures(library) == before
    empty = manager.fresh_start(store)
    assert empty["data_archive"] is empty["reports_archive"] is None
    assert data_archive.exists() and report_archive.exists()
    scan(config_file, tmp_path)
    again = manager.fresh_start(store)
    assert again["id"] != receipt["id"]
    assert data_archive.exists() and report_archive.exists()


def test_fresh_start_rebinds_another_library_and_rereads_metadata(
    outputs, config_file, library, tmp_path, capsys
):
    db, reports = outputs
    other = tmp_path / "other-music"
    shutil.copytree(library, other)
    before, other_before = signatures(library), signatures(other)
    config_file.write_text(yaml.safe_dump({"library_root": str(other)}))
    with pytest.raises(ValueError, match="different library scope"):
        scan(config_file, tmp_path)
    receipt = maintenance.archive_state(config_file, db, reports)
    assert Path(receipt["data_archive"]).joinpath(db.name).exists()
    capsys.readouterr()
    scan(config_file, tmp_path)
    assert "reread 2" in capsys.readouterr().out
    with sqlite3.connect(db) as conn:
        scope = json.loads(
            conn.execute("SELECT value FROM cache_settings WHERE key='library_scope'").fetchone()[0]
        )
        assert scope["root"] == str(other)
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
    assert signatures(library) == before and signatures(other) == other_before


@pytest.mark.parametrize(
    "lock_name",
    [
        "cache.sqlite.lock",
        "reports/.scan.lock",
        "reports/integrity-report/.scan.lock",
        "reports/identity-report/.scan.lock",
    ],
)
def test_external_scan_lock_blocks_reset_before_any_moves(
    outputs, config_file, tmp_path, lock_name
):
    db, reports = outputs
    original_db = db.read_bytes()
    original_reports = report_contents(reports)
    with exclusive_lock(tmp_path / lock_name), pytest.raises(BusyError, match="Another scan"):
        JobManager(config_file, db, reports).fresh_start(ReportStore(reports))
    assert db.read_bytes() == original_db and report_contents(reports) == original_reports
    assert not list(tmp_path.glob("previous-data-*"))
    assert not list(reports.glob("previous-reports-*"))


def test_io_failure_after_cross_directory_moves_rolls_back_everything(
    outputs, config_file, tmp_path, monkeypatch
):
    db, reports = outputs
    original_db = db.read_bytes()
    original_reports = report_contents(reports)
    rename = Path.rename
    moves = []

    def fail_once(source, destination):
        moves.append(source)
        if len(moves) == 5:
            raise OSError("injected archive failure")
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_once)
    with pytest.raises(OSError, match="moves were rolled back"):
        maintenance.archive_state(config_file, db, reports)
    assert db.read_bytes() == original_db and report_contents(reports) == original_reports
    assert not list(tmp_path.glob("previous-data-*"))
    assert not list(reports.glob("previous-reports-*"))
    assert not JobManager(config_file, db, reports).external_busy()


def test_restore_failure_keeps_archive_and_reports_precise_recovery_paths(
    outputs, config_file, monkeypatch
):
    db, reports = outputs
    original = db.read_bytes()
    rename = Path.rename
    moved = False

    def fail_after_cache(source, destination):
        nonlocal moved
        if moved:
            raise OSError("injected persistent failure")
        moved = True
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_after_cache)
    with pytest.raises(OSError, match="recovery is incomplete.*previous-data-"):
        maintenance.archive_state(config_file, db, reports)
    archived = next(db.parent.glob("previous-data-*")) / db.name
    assert archived.read_bytes() == original


@pytest.mark.parametrize("kind", ["database", "sidecar", "specialist", "current", "lock"])
def test_unsafe_links_refused_without_following_or_moving_source(
    outputs, config_file, library, kind
):
    db, reports = outputs
    audio = next(library.rglob("*.mp3"))
    before = signatures(library)
    if kind == "database":
        db.unlink()
        db.symlink_to(audio)
    elif kind == "sidecar":
        Path(str(db) + "-wal").symlink_to(audio)
    elif kind == "specialist":
        shutil.rmtree(reports / "identity-report")
        (reports / "identity-report").symlink_to(library, target_is_directory=True)
    elif kind == "current":
        (reports / "current").unlink()
        (reports / "current").symlink_to(library, target_is_directory=True)
    else:
        (reports / ".scan.lock").unlink()
        (reports / ".scan.lock").symlink_to(audio)
    with pytest.raises(ValueError, match="symlink|outside the music library"):
        maintenance.archive_state(config_file, db, reports)
    assert signatures(library) == before
    assert not list(db.parent.glob("previous-data-*"))


def test_primary_configuration_in_report_generation_is_not_archived(outputs, config_file):
    db, reports = outputs
    original = db.read_bytes()
    inside = (reports / "current").resolve() / "primary.yaml"
    inside.write_bytes(config_file.read_bytes())
    with pytest.raises(ValueError, match="configuration"):
        maintenance.archive_state(inside, db, reports)
    assert db.read_bytes() == original and inside.exists()


def test_reset_holds_real_scan_locks_and_serializes_browser_start(
    outputs, config_file, monkeypatch
):
    db, reports = outputs
    manager, store = JobManager(config_file, db, reports), ReportStore(reports)
    entered, release = threading.Event(), threading.Event()
    rename = Path.rename

    def pause(source, destination):
        if source == db:
            entered.set()
            assert release.wait(5)
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", pause)
    errors = []

    def reset():
        try:
            manager.fresh_start(store)
        except Exception as exc:  # noqa: BLE001 - assert background errors in the test thread
            errors.append(exc)

    thread = threading.Thread(target=reset)
    thread.start()
    try:
        assert entered.wait(5)
        for path in (
            Path(str(db) + ".lock"),
            reports / ".scan.lock",
            reports / "identity-report/.scan.lock",
            reports / "integrity-report/.scan.lock",
        ):
            with pytest.raises(LockBusyError), exclusive_lock(path):
                pass
        assert not manager.lock.acquire(blocking=False)
        assert not store.lock.acquire(blocking=False)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert not manager.external_busy()
