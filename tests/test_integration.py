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
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TDRC
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


def test_catalog_includes_clean_albums_discs_sizes_and_cache(tmp_path, library, config_file):
    config_file.write_text(yaml.safe_dump({
        "library_root": str(library), "required_tags": [], "artwork": {"required": False},
        "filename": {"regex": None, "allowed_chars": ""},
        "album_rules": {"consistent_album": False, "unique_disc_track": False,
                        "disc_number_mismatch": False},
    }))
    album = next(library.rglob("*.mp3")).parent.parent
    attachment = album / "cover.jpg"
    attachment.write_bytes(b"a" * 12345)
    extras = album / "extras"
    extras.mkdir()
    (extras / "notes.txt").write_bytes(b"b" * 100)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"c" * 1000)
    (album / "linked.bin").symlink_to(outside)
    before = signatures(library)
    run_scan(tmp_path, config_file)
    assert report(tmp_path)["issues"] == []
    catalog = json.loads((tmp_path / "reports/catalog.json").read_text())
    assert len(catalog["albums"]) == 1
    row = catalog["albums"][0]
    assert row["album_dir"] == str(album)
    assert row["total_tracks"] == 2
    assert row["quality"] == "MP3 64"
    assert row["folder_size_mb"] == round(sum(v[1] for v in before.values()) / 1_000_000, 2)
    assert signatures(library) == before
    assert catalog["generated_at"] == report(tmp_path)["generated_at"]
    with (tmp_path / "reports/catalog.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert csv_rows == [{key: str(value) for key, value in row.items() if key != "album_dir"}]
    # Attachment changes update sizes even when no audio needs rereading.
    attachment.write_bytes(b"a" * 2_000_000)
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    updated = json.loads((tmp_path / "reports/catalog.json").read_text())["albums"][0]
    assert updated["folder_size_mb"] > row["folder_size_mb"]
    assert updated["total_tracks"] == 2


def test_catalog_read_errors_are_counted_and_marked_unknown(tmp_path, config_file):
    with patch.object(scanner, "read_metadata", side_effect=PermissionError("unreadable")):
        run_scan(tmp_path, config_file)
    albums = json.loads((tmp_path / "reports/catalog.json").read_text())["albums"]
    assert len(albums) == 1
    assert albums[0]["total_tracks"] == 2
    assert albums[0]["quality"] == "Unknown"
    assert albums[0]["album_artist"] == albums[0]["year"] == "Unknown"


def test_catalog_refreshes_old_metadata_once_for_bitrate(tmp_path, config_file):
    run_scan(tmp_path, config_file)
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        for path, raw in con.execute("SELECT path,metadata_json FROM tracks").fetchall():
            metadata = json.loads(raw)
            metadata.pop("bitrate", None)
            con.execute("UPDATE tracks SET parser_version=3,metadata_json=? WHERE path=?",
                        (json.dumps(metadata), path))
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run_scan(tmp_path, config_file)
    assert reader.call_count == 2
    assert database.PARSER_VERSION > 3
    assert json.loads((tmp_path / "reports/catalog.json").read_text())["albums"][0]["quality"] == "MP3 64"
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)


def test_catalog_nested_album_sizes_and_track_counts(tmp_path, library, config_file):
    original = next(library.rglob("*.flac"))
    album = original.parent.parent
    nested = album / "Bonus"
    nested.mkdir()
    shutil.copyfile(original, nested / "01 - Bonus.flac")
    run_scan(tmp_path, config_file)
    rows = json.loads((tmp_path / "reports/catalog.json").read_text())["albums"]
    assert [row["album_dir"] for row in rows] == [str(album), str(nested)]
    assert [row["total_tracks"] for row in rows] == [2, 1]
    assert [row["quality"] for row in rows] == ["MP3 64", "FLAC"]
    for row, directory in zip(rows, [album, nested]):
        size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        assert row["folder_size_mb"] == round(size / 1_000_000, 2)


def test_empty_library_has_empty_catalog(tmp_path):
    library = tmp_path / "empty"
    library.mkdir()
    config_file = tmp_path / "empty.yaml"
    config_file.write_text(yaml.safe_dump({"library_root": str(library)}))
    run_scan(tmp_path, config_file)
    assert json.loads((tmp_path / "reports/catalog.json").read_text())["albums"] == []
    assert "No albums found" in (tmp_path / "reports/catalog.html").read_text()
    with (tmp_path / "reports/catalog.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert len(reader.fieldnames) == 6
        assert list(reader) == []


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


def test_conditional_rules_normalization_cache_and_reports(tmp_path, custom_rule_library):
    config_file = tmp_path / "custom.yaml"
    custom = {
        "id": "comment_lyricist",
        "field": "comment",
        "when": {"field": "lyricist", "is_present": True},
        "template": {"operator": "contains", "pattern": "słowa: {lyricist}"},
    }
    config = {"library_root": str(custom_rule_library), "custom_rules": [custom]}
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    before = signatures(custom_rule_library)
    run_scan(tmp_path, config_file)
    custom_issues = [i for i in report(tmp_path)["issues"] if i["rule"] == "comment_lyricist"]
    assert len(custom_issues) == 1 and custom_issues[0]["path"].endswith(".flac")
    # Simulate cached parser-2 metadata that omitted these MP3 fields.
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        for path, meta_json in con.execute("SELECT path,metadata_json FROM tracks").fetchall():
            meta = json.loads(meta_json)
            for key in ("lyricist", "origlyricist", "compilation"):
                meta["tags"].pop(key, None)
            con.execute(
                "UPDATE tracks SET metadata_json=?,parser_version=2 WHERE path=?",
                (json.dumps(meta), path),
            )
    with patch.object(scanner, "read_metadata", wraps=scanner.read_metadata) as reader:
        run_scan(tmp_path, config_file)
    assert reader.call_count == 2
    for record in records(tmp_path):
        tags = json.loads(record[2])["tags"]
        assert tags["lyricist"] == "Żaneta Nowak" and tags["origlyricist"] == "Jan Łódzki"
    custom["template"]["pattern"] = "słowa: {origlyricist}"
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert sum(i["rule"] == "comment_lyricist" for i in report(tmp_path)["issues"]) == 2
    custom["when"] = {"format": "mp3", "is_compilation": True, "field": "title", "contains": "Łódź"}
    custom["template"] = {"operator": "regex", "pattern": r"muzyka: Anna; słowa: {lyricist}; live"}
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert not any(i["rule"] == "comment_lyricist" for i in report(tmp_path)["issues"])
    custom["field"] = "lyricist"
    custom["template"] = {"operator": "equals", "pattern": "{lyricist}"}
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    assert not any(i["rule"] == "comment_lyricist" for i in report(tmp_path)["issues"])
    assert signatures(custom_rule_library) == before
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == report(tmp_path)["issues"]
    html = (tmp_path / "reports/index.html").read_text()
    assert html.count("<tr>") - html.count("<thead>") == len(report(tmp_path)["issues"])


def test_required_year_uses_dates_in_existing_cache_without_audio_reads(tmp_path, library, config_file):
    for ext, date in [("mp3", "2001-02-03"), ("flac", "2002")]:
        source = BytesIO(next(library.rglob(f"*.{ext}")).read_bytes())
        if ext == "mp3":
            audio = ID3(source)
            audio.add(TDRC(encoding=3, text=[date]))
            audio.save(source)
        else:
            audio = FLAC(source)
            audio["date"] = [date]
            source.seek(0)
            audio.save(source)
        target = library / f"Dated {ext}" / f"01 - Dated.{ext}"
        target.parent.mkdir()
        target.write_bytes(source.getvalue())  # New synthetic fixture, not a music-file edit.
    before = signatures(library)
    run_scan(tmp_path, config_file)
    # Emulate the old cache model, which stored DATE but no derived YEAR.
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        for path, meta_json in con.execute("SELECT path,metadata_json FROM tracks").fetchall():
            meta = json.loads(meta_json)
            meta["tags"].pop("year", None)
            con.execute("UPDATE tracks SET metadata_json=? WHERE path=?", (json.dumps(meta), path))
    config = yaml.safe_load(config_file.read_text())
    config["required_tags"] = ["year"]
    config["custom_rules"] = [{"id": "year_in_date", "when": {"field": "year", "is_present": True},
                              "field": "date", "template": {"operator": "contains", "pattern": "{year}"}}]
    config_file.write_text(yaml.safe_dump(config))
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        run_scan(tmp_path, config_file)
    issues = report(tmp_path)["issues"]
    missing = [i for i in issues if i["rule"] == "missing_year"]
    assert len(missing) == 2  # The two original fixtures genuinely have no date/year.
    assert all("Dated" not in i["path"] for i in missing)
    assert not any(i["rule"] == "year_in_date" for i in issues)
    assert signatures(library) == before
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == issues
