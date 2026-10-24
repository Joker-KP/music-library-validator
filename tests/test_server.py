import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
import yaml
from conftest import signatures

from music_validator import cli, jobs, scanner
from music_validator.jobs import ACTIVE, BusyError, JobManager, validate_options
from music_validator.report_store import ReportStore
from music_validator.safety import exclusive_lock
from music_validator.server import ReportServer


@pytest.fixture
def manager(config_file, tmp_path):
    value = JobManager(config_file, tmp_path / "cache.sqlite", tmp_path / "reports")
    yield value
    value.close()


@pytest.fixture
def http_server(config_file, tmp_path):
    server = ReportServer(
        ("127.0.0.1", 0), config_file, tmp_path / "cache.sqlite", tmp_path / "reports"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def request(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    defaults = (
        {} if data is None else {"Content-Type": "application/json", "X-Music-Validator": "1"}
    )
    req = Request(base + path, data=data, headers={**defaults, **(headers or {})})
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as exc:
        response = exc
    with response:
        content = response.read()
        return response.status, (
            json.loads(content)
            if response.headers.get_content_type() == "application/json"
            else content
        )


def finished(manager):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = manager.snapshot()["job"]
        if state and state["status"] not in ACTIVE:
            return state
        time.sleep(0.02)
    pytest.fail("Worker did not finish")


def scan(config_file, tmp_path, **kwargs):
    cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports", **kwargs)


def test_assets_empty_reports_and_restricted_routes(http_server):
    _, base = http_server
    assert b"SCAN CONTROL" in request(base, "/")[1]
    assert b"innerHTML" not in request(base, "/app.js")[1]
    assert request(base, "/style.css")[0] == 200
    assert all(not s["available"] for s in request(base, "/api/reports")[1].values())
    assert request(base, "/api/reports/configured/albums")[0] == 404
    for path in ("/../../config/rules.yaml", "/data/cache.sqlite", "/music/file.mp3"):
        assert request(base, path)[0] == 404
    assert request(base, "/reports/configured/../../config/rules.yaml")[0] == 404
    assert request(base, "/reports/configured/scan-test/cache.sqlite")[0] == 400
    assert request(base, "/api/jobs", {}, {"Origin": "http://elsewhere.test"})[0] == 403
    assert request(base, "/api/jobs", {}, {"X-Music-Validator": ""})[0] == 403
    assert request(base, "/api/jobs", {"mode": "shell"})[0] == 400
    assert request(base, "/api/jobs", {"full_scan": "yes"})[0] == 400
    assert request(base, "/api/jobs", {"command": "anything"})[0] == 400


def test_real_http_scan_cli_revalidation_pinned_reports_and_source_safety(
    http_server, config_file, library, tmp_path, monkeypatch
):
    server, base = http_server
    before = signatures(library)
    code, started = request(base, "/api/jobs", {"mode": "configured"})
    assert code == 202 and started["status"] == "running"
    job = finished(server.jobs)
    assert job["status"] == "completed_with_findings"
    assert job["cache_committed"] and job["reports_published"] and job["issue_count"] > 0
    assert job["progress"]["phase"] == "Publishing reports"
    summary = request(base, "/api/reports")[1]["configured"]
    assert summary["albums"] == 1 and summary["tracks"] == 2
    assert summary["affected_tracks"] == 2
    generation = summary["generation"]
    old_report = request(base, f"/reports/configured/{generation}/report.json")[1]
    assert isinstance(old_report, bytes)
    catalog = request(base, "/api/reports/configured/albums?page_size=1")[1]
    assert catalog["total"] == 1 and len(catalog["items"]) == 1
    album = catalog["items"][0]["album_dir"]
    page = request(
        base,
        "/api/reports/configured/issues?"
        + urlencode({"album": album, "page_size": 1, "generation": generation}),
    )[1]
    assert page["matching"] == summary["issues"] and len(page["items"]) == 1
    assert request(base, "/api/reports/configured/albums?q=does-not-exist")[1]["matching"] == 0
    config_file.write_text(yaml.safe_dump({"library_root": str(library), "required_tags": []}))
    monkeypatch.setattr(scanner, "read_metadata", lambda *a, **kw: pytest.fail("Audio reread"))
    scan(config_file, tmp_path)
    latest = request(base, "/api/reports")[1]["configured"]
    assert latest["generation"] != generation
    assert request(base, f"/reports/configured/{generation}/report.json")[1] == old_report
    assert request(base, f"/api/reports/configured/summary?generation={generation}")[1] == summary
    assert signatures(library) == before
    assert not request(base, "/api/jobs")[1]["external_busy"]


def test_cli_ownership_visible_and_server_start_rejected(http_server, tmp_path):
    _, base = http_server
    with exclusive_lock(tmp_path / "cache.sqlite.lock"):
        assert request(base, "/api/jobs")[1]["external_busy"]
        assert request(base, "/api/jobs", {"mode": "integrity"})[0] == 409
    assert not request(base, "/api/jobs")[1]["external_busy"]


def test_confirmed_http_fresh_start_archives_and_clears_old_job_and_reports(http_server, tmp_path):
    server, base = http_server
    request(base, "/api/jobs", {"mode": "configured"})
    assert finished(server.jobs)["reports_published"]
    summary = request(base, "/api/reports")[1]["configured"]
    assert server.store.cache
    path = "/api/maintenance/fresh-start"
    for body in (
        {},
        {"confirmation": True},
        {"confirmation": "fresh start"},
        {"confirmation": "FRESH START", "db": "/another.sqlite"},
    ):
        assert request(base, path, body)[0] == 400
    assert (
        request(base, path, {"confirmation": "FRESH START"}, {"Origin": "http://elsewhere.test"})[0]
        == 403
    )
    assert request(base, path, {"confirmation": "FRESH START"}, {"X-Music-Validator": ""})[0] == 403
    assert (tmp_path / "cache.sqlite").exists()
    code, receipt = request(base, path, {"confirmation": "FRESH START"})
    assert code == 200
    assert receipt["data_archive"].startswith(str(tmp_path / "previous-data-"))
    assert receipt["reports_archive"].startswith(str(tmp_path / "reports/previous-reports-"))
    status = request(base, "/api/jobs")[1]
    assert status["job"] is None and status["reset"] == receipt
    assert not status["external_busy"] and not server.store.cache
    assert all(not r["available"] for r in request(base, "/api/reports")[1].values())
    assert request(base, f"/reports/configured/{summary['generation']}/report.json")[0] == 404
    assert request(base, "/api/jobs", {"mode": "configured"})[0] == 202
    assert finished(server.jobs)["reports_published"]
    assert request(base, "/api/reports")[1]["configured"]["tracks"] == 2


def test_http_fresh_start_refuses_active_server_and_cli_jobs(http_server, tmp_path, monkeypatch):
    server, base = http_server
    path = "/api/maintenance/fresh-start"
    confirmation = {"confirmation": "FRESH START"}
    with exclusive_lock(tmp_path / "cache.sqlite.lock"):
        assert request(base, path, confirmation)[0] == 409
    original = subprocess.Popen

    def launch(argv, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    job = server.jobs.start({})
    assert request(base, path, confirmation)[0] == 409
    server.jobs.cancel(job["id"])
    assert finished(server.jobs)["status"] == "cancelled"
    assert not list(tmp_path.glob("previous-data-*"))
    assert not list((tmp_path / "reports").glob("previous-reports-*"))


def test_failed_configuration_preserves_previous_results(manager, config_file, tmp_path, library):
    scan(config_file, tmp_path)
    current = (tmp_path / "reports/current").readlink()
    (library / "rules.yaml").write_text("artwork: null")
    manager.start({"mode": "configured"})
    job = finished(manager)
    assert job["status"] == "failed" and "rules.yaml" in job["error"]
    assert not job["cache_committed"] and not job["reports_published"]
    assert (tmp_path / "reports/current").readlink() == current


def test_publication_failure_keeps_committed_cache_and_previous_report(
    manager, config_file, tmp_path, monkeypatch
):
    scan(config_file, tmp_path)
    current = (tmp_path / "reports/current").readlink()
    original = subprocess.Popen

    def launch(argv, **kwargs):
        code = (
            "import sys; from music_validator import cli; "
            "cli.write_reports = lambda *a, **kw: (_ for _ in ()).throw(OSError('disk full')); "
            "from music_validator.worker import main; sys.exit(main())"
        )
        return original([argv[0], "-c", code, *argv[3:]], **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    manager.start({})
    job = finished(manager)
    assert job["status"] == "failed" and job["error"] == "disk full"
    assert job["cache_committed"] and not job["reports_published"]
    assert (tmp_path / "reports/current").readlink() == current
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 2


def test_cancel_duplicate_start_and_retry_are_isolated_and_release_worker(manager, monkeypatch):
    original = subprocess.Popen

    def launch(argv, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    first = manager.start({"mode": "identity", "recheck_identity": True})
    with pytest.raises(BusyError):
        manager.start({"mode": "configured"})
    with pytest.raises(KeyError):
        manager.cancel("foreign-job")
    assert manager.cancel(first["id"])["status"] == "cancelling"
    assert finished(manager)["status"] == "cancelled"
    assert manager.process.poll() is not None
    with pytest.raises(BusyError):
        manager.cancel(first["id"])
    second = manager.start(first["options"])
    assert second["id"] != first["id"]
    manager.close()
    assert finished(manager)["status"] == "cancelled"


def test_cancelling_real_scan_rolls_back_and_releases_database(
    manager, config_file, tmp_path, library, monkeypatch
):
    scan(config_file, tmp_path)
    previous = (tmp_path / "reports/current").readlink()
    before = signatures(library)
    original = subprocess.Popen

    def launch(argv, **kwargs):
        code = (
            "import sys, time; from music_validator import scanner; "
            "original = scanner.read_metadata; "
            "scanner.read_metadata = lambda *a, **kw: (time.sleep(60) or original(*a, **kw)); "
            "from music_validator.worker import main; sys.exit(main())"
        )
        return original([argv[0], "-c", code, *argv[3:]], **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    job = manager.start({"full_scan": True})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        progress = manager.snapshot()["job"]["progress"]
        if progress and progress["phase"] == "Analyzing tracks":
            break
        time.sleep(0.02)
    else:
        pytest.fail("Worker never entered its scan transaction")
    manager.cancel(job["id"])
    result = finished(manager)
    assert result["status"] == "cancelled" and not result["cache_committed"]
    assert (tmp_path / "reports/current").readlink() == previous
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
    scan(config_file, tmp_path)
    assert signatures(library) == before


@pytest.mark.parametrize(
    "value",
    [
        [],
        None,
        {"mode": "bad"},
        {"full_scan": 1},
        {"mode": "identity", "recheck_integrity": True},
        {"mode": "integrity", "recheck_identity": True},
        {"db": "/another.sqlite"},
    ],
)
def test_job_options_are_closed_and_typed(value):
    with pytest.raises(ValueError):
        validate_options(value)


def test_report_filters_sort_pagination_and_unsafe_generations(config_file, tmp_path, library):
    scan(config_file, tmp_path)
    store = ReportStore(tmp_path / "reports")
    summary = store.query("configured", "summary", {})
    rule = next(iter(summary["rules"]))
    issues = store.query("configured", "issues", {"rule": rule, "page_size": "1"})
    assert issues["matching"] == summary["rules"][rule] and len(issues["items"]) == 1
    assert store.query("configured", "albums", {"rule": rule})["matching"] == 1
    assert store.query("configured", "albums", {"q": "Żółć"})["matching"] == 1
    assert store.query("configured", "albums", {"q": "folder_size_mb"})["matching"] == 0
    assert store.query("configured", "issues", {"severity": "not-a-severity"})["matching"] == 0
    for params in (
        {"page_size": "201"},
        {"page": "0"},
        {"page": "x"},
        {"generation": "../music"},
        {"sort": "metadata_json"},
    ):
        with pytest.raises(ValueError):
            store.query("configured", "issues", params)
    generation = (tmp_path / "reports/current").resolve()
    (generation / "issues.csv").unlink()
    (generation / "issues.csv").symlink_to(next(library.rglob("*.mp3")))
    with pytest.raises(ValueError, match="symlink"):
        store.query("configured", "summary", {})


@contextmanager
def fake_audio_worker(monkeypatch):
    original = subprocess.Popen

    def launch(argv, **kwargs):
        code = (
            "import sys; sys.path.insert(0, 'tests'); "
            "from music_validator import cli; "
            "from music_validator.integrity import selected_checks; "
            "from test_audio_integration import FakeBackend; "
            "from test_integrity_integration import FakeIntegrity; "
            "cli.FingerprintBackend = FakeBackend; "
            "backend = FakeIntegrity(); "
            "cli.IntegrityBackend = lambda options, extensions: "
            "(setattr(backend, 'checks', {e: selected_checks(options, e) for e in extensions}) or backend); "
            "from music_validator.worker import main; sys.exit(main())"
        )
        return original([argv[0], "-c", code, *argv[3:]], **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    yield


def test_all_modes_independent_results_and_success_without_findings(
    manager, config_file, tmp_path, library, monkeypatch
):
    config_file.write_text(
        yaml.safe_dump(
            {"library_root": str(library), "audio_identity_rules": {"fields": ["title"]}}
        )
    )
    store = ReportStore(tmp_path / "reports")
    manager.start({"mode": "configured"})
    assert finished(manager)["status"] == "completed_with_findings"
    normal = store.load("configured")["summary"]
    with fake_audio_worker(monkeypatch):
        manager.start({"mode": "integrity"})
        assert finished(manager)["status"] == "completed"
        integrity = store.load("integrity")["summary"]
        assert integrity["analysis"]["statistics"]["checks_executed"] == 3
        assert store.query("integrity", "results", {"status": "passed"})["matching"] == 3
        manager.start({"mode": "identity", "recheck_identity": True})
        assert finished(manager)["status"] == "completed"
        assert store.query("identity", "groups", {})["matching"] == 1
        assert store.query("identity", "groups", {"conflicts_only": "true"})["matching"] == 0
        assert store.query("identity", "results", {"status": "matched"})["matching"] == 2
        assert store.load("configured")["summary"] == normal
        assert store.load("integrity")["summary"] == integrity
