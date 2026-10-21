import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from conftest import signatures
from mutagen.flac import Picture
from PIL import Image

from music_validator import cli, database, scanner


def run_scan(tmp_path, config_file, full=False):
    return cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports", full)


def records(tmp_path):
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        return con.execute(
            "SELECT path,album_dir,metadata_json,error FROM tracks ORDER BY path"
        ).fetchall()


def report(tmp_path):
    return json.loads((tmp_path / "reports/report.json").read_text())


def test_real_formats_cache_rules_grouping_full_and_read_only(
    tmp_path, library, config_file, capsys
):
    before = signatures(library)
    assert run_scan(tmp_path, config_file) == 0
    assert "reread 2" in capsys.readouterr().out
    assert len({r[1] for r in records(tmp_path)}) == 1
    assert {json.loads(r[2])["format"] for r in records(tmp_path)} == {"mp3", "flac"}
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
        assert "reread 0" in capsys.readouterr().out
        config = yaml.safe_load(config_file.read_text())
        config["required_tags"] = ["genre"]
        config["disc_folder_pattern"] = r"^Disc-\d+$"
        config_file.write_text(yaml.safe_dump(config))
        run_scan(tmp_path, config_file)
    assert len({r[1] for r in records(tmp_path)}) == 2
    assert sum(i["rule"] == "missing_genre" for i in report(tmp_path)["issues"]) == 2
    assert not any(i["rule"] == "duplicate_track" for i in report(tmp_path)["issues"])
    run_scan(tmp_path, config_file, full=True)
    assert "reread 2" in capsys.readouterr().out
    assert signatures(library) == before
    issues = report(tmp_path)["issues"]
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == issues
    html = (tmp_path / "reports/index.html").read_text()
    assert html.count("<tbody>") == 2
    assert html.count("<tr>") - html.count("<thead>") == len(issues)


def test_subprocess_cli(tmp_path, config_file):
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
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Indexed 2 tracks; reread 2" in result.stdout


def test_discovery_failure_rolls_back_and_preserves_reports(tmp_path, library, config_file):
    run_scan(tmp_path, config_file)
    before = records(tmp_path)
    current = (tmp_path / "reports/current").readlink()
    real_scandir = os.scandir

    # Discovery uses directory descriptors; inspect the descriptor's target.
    def denied(path):
        name = os.readlink(f"/proc/self/fd/{path}") if isinstance(path, int) else str(path)
        if Path(name).name == "CD2":
            raise PermissionError("simulated traversal failure")
        return real_scandir(path)

    with patch("os.scandir", side_effect=denied), pytest.raises(OSError):
        run_scan(tmp_path, config_file, full=True)
    assert records(tmp_path) == before
    assert (tmp_path / "reports/current").readlink() == current


def test_transient_errors_are_retried(tmp_path, config_file):
    with patch.object(scanner, "read_metadata", side_effect=PermissionError("transient")):
        run_scan(tmp_path, config_file)
    assert sum(i["rule"] == "read_error" for i in report(tmp_path)["issues"]) == 2
    run_scan(tmp_path, config_file)
    assert all(not r[3] for r in records(tmp_path))


def test_delete_only_after_successful_scan(tmp_path, library, config_file):
    run_scan(tmp_path, config_file)
    next(library.rglob("*.mp3")).unlink()  # Deliberate fixture-only external removal.
    run_scan(tmp_path, config_file)
    assert len(records(tmp_path)) == 1


def test_symlink_files_and_directories_skipped(tmp_path, library, config_file):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.mp3").write_bytes(b"not audio")
    (library / "linked-dir").symlink_to(outside, target_is_directory=True)
    (library / "linked.mp3").symlink_to(outside / "outside.mp3")
    run_scan(tmp_path, config_file)
    assert len(records(tmp_path)) == 2


def test_interruption_rolls_back_and_releases_lock(tmp_path, config_file):
    run_scan(tmp_path, config_file)
    before = records(tmp_path)
    real_upsert = database.upsert

    def interrupted(*args, **kwargs):
        real_upsert(*args, **kwargs)
        raise KeyboardInterrupt

    with (
        patch.object(database, "upsert", side_effect=interrupted),
        pytest.raises(KeyboardInterrupt),
    ):
        run_scan(tmp_path, config_file, full=True)
    assert records(tmp_path) == before
    run_scan(tmp_path, config_file)


def test_changed_source_during_read_is_not_cached_as_good(tmp_path, config_file):
    real_reader = scanner.read_metadata
    real_fstat = os.fstat
    after_parse = set()

    def reader(path, fileobj):
        metadata = real_reader(path, fileobj=fileobj)
        after_parse.add(fileobj.fileno())
        return metadata

    def fstat(fd):
        result = real_fstat(fd)
        if fd in after_parse:
            after_parse.remove(fd)
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_size=result.st_size + 1,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    with (
        patch.object(scanner, "read_metadata", side_effect=reader),
        patch("os.fstat", side_effect=fstat),
    ):
        run_scan(tmp_path, config_file)
    assert any(r[3] for r in records(tmp_path))


def test_audio_opens_are_read_only(tmp_path, config_file):
    real_open = os.open
    audio_opens = []

    def checked_open(path, flags, *args, **kwargs):
        if Path(path).suffix.lower() in (".mp3", ".flac"):
            audio_opens.append(flags)
            assert flags & os.O_ACCMODE == os.O_RDONLY
            assert flags & os.O_NOFOLLOW
            assert kwargs.get("dir_fd") is not None
        return real_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=checked_open):
        run_scan(tmp_path, config_file)
    assert len(audio_opens) == 2


def test_hostile_filenames_remain_data(tmp_path, library, config_file):
    path = library / "O'Brien <script>" / "bad' name.mp3"
    path.parent.mkdir()
    shutil.copyfile(next(library.rglob("*.mp3")), path)
    run_scan(tmp_path, config_file)
    assert len(records(tmp_path)) == 3
    assert str(path) in {i["path"] for i in report(tmp_path)["issues"]}
    assert "<script>" not in (tmp_path / "reports/index.html").read_text()


def test_discovery_is_lazy_and_reports_directory_failures(library):
    iterator = scanner.discover(library, {".mp3", ".flac"})
    real_scandir = os.scandir

    def denied(path):
        name = os.readlink(f"/proc/self/fd/{path}")
        if Path(name).name == "CD2":
            raise PermissionError("unreadable second subtree")
        return real_scandir(path)

    with patch("os.scandir", side_effect=denied):
        assert next(iterator)[0].suffix == ".mp3"
        with pytest.raises(PermissionError):
            next(iterator)


def test_publication_failure_keeps_prior_reports_and_recovers_from_cache(tmp_path, config_file):
    run_scan(tmp_path, config_file)
    current = (tmp_path / "reports/current").readlink()
    with (
        patch.object(cli, "write_reports", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        run_scan(tmp_path, config_file)
    assert (tmp_path / "reports/current").readlink() == current
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 2
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert (tmp_path / "reports/current").readlink() != current


def test_disc_suffix_and_character_rule_revalidate_cached_metadata(tmp_path, library, config_file):
    # Construct suffix-named directories from fixtures before scanning.
    for disc, ext in [(1, "mp3"), (2, "flac")]:
        directory = library / "Artist" / "Album 1999" / f"Album 1999 - CD{disc}"
        directory.mkdir(parents=True)
        shutil.copyfile(next(library.rglob(f"*.{ext}")), directory / f"01 - Song_[Live].{ext}")
    before = signatures(library)
    config = yaml.safe_load(config_file.read_text())
    config["disc_folder_pattern"] = r"^(?:cd|disc|disk)[ _.-]*0*[1-9][0-9]*$"
    config_file.write_text(yaml.safe_dump(config))
    run_scan(tmp_path, config_file)
    assert len({r[1] for r in records(tmp_path)}) == 3
    assert sum(i["rule"] == "filename_allowed_chars" for i in report(tmp_path)["issues"]) == 4
    del config["disc_folder_pattern"]
    config["filename"] = {"allowed_chars": ""}
    config_file.write_text(yaml.safe_dump(config))
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert len({r[1] for r in records(tmp_path)}) == 2
    assert not any(
        i["rule"] in ("duplicate_track", "filename_allowed_chars")
        for i in report(tmp_path)["issues"]
    )
    assert signatures(library) == before
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == report(tmp_path)["issues"]


def test_flac_zero_picture_dimensions_refresh_old_cache(tmp_path, library, config_file):
    # Build a FLAC fixture with a real picture block, preserving the synthetic
    # fixture's other metadata and audio bytes. No existing music file is edited.
    path = library / "Artwork" / "01 - Song.flac"
    path.parent.mkdir()
    output = BytesIO()
    Image.new("RGB", (800, 800)).save(output, format="JPEG")
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.data = output.getvalue()
    assert picture.width == picture.height == 0
    block = picture.write()
    source = next(library.rglob("*.flac")).read_bytes()
    assert source.startswith(b"fLaC")
    # Preserve the required first STREAMINFO block, then add a non-final PICTURE.
    assert source[4] == 0
    first_end = 8 + int.from_bytes(source[5:8], "big")
    path.write_bytes(
        source[:first_end] + b"\x06" + len(block).to_bytes(3, "big") + block + source[first_end:]
    )
    before = signatures(library)
    run_scan(tmp_path, config_file)
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        record = con.execute(
            "SELECT metadata_json FROM tracks WHERE path=?", (str(path),)
        ).fetchone()
        old = json.loads(record[0])
        old["artwork"][0].update(width=0, height=0)
        con.execute(
            "UPDATE tracks SET parser_version=1, metadata_json=? WHERE path=?",
            (json.dumps(old), str(path)),
        )
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run_scan(tmp_path, config_file)
    assert reader.call_count == 1
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        meta, version = con.execute(
            "SELECT metadata_json,parser_version FROM tracks WHERE path=?", (str(path),)
        ).fetchone()
    assert version == database.PARSER_VERSION > 1
    assert json.loads(meta)["artwork"][0]["width"] == 800
    assert json.loads(meta)["artwork"][0]["height"] == 800
    assert not any(
        i["path"] == str(path)
        and i["rule"] in ("small_artwork", "read_error", "artwork_dimensions_unavailable")
        for i in report(tmp_path)["issues"]
    )
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert signatures(library) == before
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == report(tmp_path)["issues"]
