import csv
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from music_validator.reports import write_reports
from music_validator.safety import REPORT_FILES


def issues(message):
    return [
        {
            "album": "/music/<b>A</b>",
            "path": "/music/<script>x</script>.mp3",
            "rule": "test",
            "severity": "warning",
            "message": message,
        }
    ]


@pytest.mark.parametrize("filename", ["catalog.html", "catalog.json", "catalog.csv"])
def test_catalog_failure_preserves_all_six_outputs(tmp_path, filename):
    dest = tmp_path / "reports"
    write_reports(dest, issues("old"), 1)
    before = {name: (dest / name).read_bytes() for name in REPORT_FILES}
    current = (dest / "current").readlink()
    real_open = Path.open

    def fail_catalog(path, mode="r", *args, **kwargs):
        if path.name == filename and "w" in mode:
            raise OSError("catalog disk full")
        return real_open(path, mode, *args, **kwargs)

    with patch.object(Path, "open", fail_catalog), pytest.raises(OSError):
        write_reports(dest, issues("new"), 1)
    assert (dest / "current").readlink() == current
    assert all((dest / name).read_bytes() == value for name, value in before.items())


def test_catalog_html_escapes_metadata_and_folder_attributes(tmp_path):
    attack = '<script>alert("x")</script>'
    row = {
        "album_dir": '" onmouseover="alert(1)',
        "album_artist": attack,
        "year": "2001",
        "album": "Żółć & Łódź",
        "quality": "FLAC",
        "folder_size_mb": 1.25,
        "total_tracks": 2,
    }
    write_reports(tmp_path, [], 2, [row])
    document = (tmp_path / "catalog.html").read_text()
    assert attack not in document
    assert "&lt;script&gt;" in document
    assert "Żółć &amp; Łódź" in document
    assert 'title="&quot; onmouseover=&quot;alert(1)"' in document
    assert document.count('<th scope="col">') == 6
    assert json.loads((tmp_path / "catalog.json").read_text())["albums"] == [row]


def test_adding_catalog_to_existing_three_file_generation(tmp_path):
    dest = tmp_path / "reports"
    old = dest / "generations/scan-old"
    old.mkdir(parents=True)
    for name in ("index.html", "issues.csv", "report.json"):
        (old / name).write_text("old")
        (dest / name).symlink_to(Path("current") / name)
    (dest / "current").symlink_to("generations/scan-old")
    write_reports(dest, [], 0)
    assert all((dest / name).is_file() for name in REPORT_FILES)
    assert all(
        (old / name).read_text() == "old" for name in ("index.html", "issues.csv", "report.json")
    )
    assert json.loads((dest / "catalog.json").read_text())["albums"] == []


def test_atomic_report_failure_preserves_previous_generation(tmp_path):
    dest = tmp_path / "reports"
    write_reports(dest, issues("old"), 1)
    before = {
        name: (dest / name).read_bytes() for name in ("index.html", "issues.csv", "report.json")
    }
    current = (dest / "current").readlink()
    real_write_text = Path.write_text

    def fail_html(path, *args, **kwargs):
        if path.name == "index.html":
            raise OSError("simulated disk full")
        return real_write_text(path, *args, **kwargs)

    with patch.object(Path, "write_text", fail_html), pytest.raises(OSError):
        write_reports(dest, issues("new"), 1)
    assert (dest / "current").readlink() == current
    assert all((dest / name).read_bytes() == contents for name, contents in before.items())
    write_reports(dest, issues("new"), 1)
    assert (dest / "current").readlink() != current


def test_legacy_outputs_upgrade_and_html_escaping(tmp_path):
    dest = tmp_path / "reports"
    dest.mkdir()
    for name in ("index.html", "issues.csv", "report.json"):
        (dest / name).write_text("legacy")
    attack = '<img src=x onerror="alert(1)">'
    write_reports(dest, issues(attack), 1)
    assert attack not in (dest / "index.html").read_text()
    assert "&lt;script&gt;" in (dest / "index.html").read_text()
    assert json.loads((dest / "report.json").read_text())["issues"] == issues(attack)
    with (dest / "issues.csv").open(encoding="utf-8-sig", newline="") as f:
        assert list(csv.DictReader(f)) == issues(attack)


def test_failed_pointer_switch_preserves_previous_reports(tmp_path):
    dest = tmp_path / "reports"
    write_reports(dest, issues("old"), 1)
    old = (dest / "current").readlink()
    with (
        patch("music_validator.reports.os.replace", side_effect=OSError("failure")),
        pytest.raises(OSError),
    ):
        write_reports(dest, issues("new"), 1)
    assert (dest / "current").readlink() == old


def test_unexpected_current_symlink_rejected(tmp_path):
    dest = tmp_path / "reports"
    dest.mkdir()
    (dest / "current").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        write_reports(dest, issues("x"), 1)


@pytest.mark.parametrize("failure", [1, 2, 3, 4, 5])
def test_interrupted_legacy_migration_preserves_all_files(tmp_path, failure):
    dest = tmp_path / "reports"
    dest.mkdir()
    original = {name: "legacy " + name for name in ("index.html", "issues.csv", "report.json")}
    for name, contents in original.items():
        (dest / name).write_text(contents)
    real_replace = os.replace
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == failure:
            raise OSError("interrupted legacy migration")
        return real_replace(*args)

    with (
        patch("music_validator.reports.os.replace", side_effect=interrupted),
        pytest.raises(OSError),
    ):
        write_reports(dest, issues("new"), 1)
    assert all((dest / name).read_text() == contents for name, contents in original.items())
    write_reports(dest, issues("new"), 1)
    assert json.loads((dest / "report.json").read_text())["issues"] == issues("new")
