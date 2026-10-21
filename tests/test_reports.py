import csv
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from music_validator.reports import write_reports


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
