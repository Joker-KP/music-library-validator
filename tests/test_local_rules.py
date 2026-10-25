import csv
import json
import os
import shutil
import sqlite3
from copy import deepcopy
from unittest.mock import patch

import pytest
import yaml
from conftest import signatures

from music_validator import cli, scanner
from music_validator.config import DEFAULTS, validate_config
from music_validator.local_rules import LocalRules, apply_patch
from music_validator.validation import validate


def rule(identifier="credit", pattern="expected"):
    return {
        "id": identifier,
        "field": "comment",
        "template": {"operator": "contains", "pattern": pattern},
    }


def base_config(root="/music"):
    return validate_config(
        {
            **deepcopy(DEFAULTS),
            "library_root": str(root),
            "custom_rules": [rule()],
        }
    )


def write_patch(directory, value):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "rules.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return path


def test_patch_merging_order_replacement_and_parent_isolation():
    parent = base_config()
    before = deepcopy(parent)
    result = apply_patch(
        parent,
        {
            "added_required_tags": ["composer", "title"],
            "removed_required_tags": ["comment", "absent"],
            "removed_custom_rules": ["credit"],
            "added_custom_rules": [rule("other"), rule(pattern="replacement")],
            "artwork": {"required": False},
            "album_rules": {"consistent_artwork": True},
        },
    )
    assert parent == before
    assert result["required_tags"] == ["title", "artist", "album", "tracknumber", "composer"]
    assert [r["id"] for r in result["custom_rules"]] == ["other", "credit"]
    assert result["custom_rules"][1]["template"]["pattern"] == "replacement"
    assert result["artwork"] == {"required": False, "min_width": 500, "min_height": 500}
    assert result["album_rules"]["consistent_album"] is True
    assert result["album_rules"]["consistent_artwork"] is True
    restored = apply_patch(result, {"added_required_tags": ["comment"]})
    assert restored["required_tags"][-1] == "comment"


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"filename": None},
        {"filename": {"regex": "["}},
        {"filename": {"regex": True}},
        {"filename": {"regex": 123}},
        {"filename": {"regex": "a" * 4097}},
        {"filename": {"pattern": ".*"}},
        {"filename": {"allowed_chars": ""}},
        {"filename": {"match_template": None}},
        {"library_root": "/elsewhere"},
        {"exclude_directory_patterns": ["#recycle"]},
        {"required_tags": []},
        {"audio_integrity": {"enabled": True}},
        {"added_required_tags": None},
        {"removed_required_tags": "comment"},
        {"added_required_tags": ["Title"]},
        {"added_required_tags": ["title", "title"]},
        {"added_required_tags": ["comment"], "removed_required_tags": ["comment"]},
        {"removed_custom_rules": ["unknown"]},
        {"removed_custom_rules": ["credit", "credit"]},
        {"added_custom_rules": [rule()]},
        {"added_custom_rules": [rule("new"), rule("new")]},
        {"added_custom_rules": [{"id": "new", "field": "comment"}]},
        {"added_custom_rules": [rule("Bad")]},
        {"added_custom_rules": [rule("new", "{title.__class__}")]},
        {"added_custom_rules": [{**rule("new"), "when": {"format": "wav"}}]},
        {"artwork": None},
        {"artwork": {"required": "false"}},
        {"artwork": {"min_width": -1}},
        {"artwork": {"unknown": True}},
        {"album_rules": None},
        {"album_rules": {"consistent_album": 1}},
    ],
)
def test_invalid_patch(value):
    with pytest.raises(ValueError):
        apply_patch(base_config(), value)


def test_nested_directories_without_audio_and_sibling_isolation(tmp_path):
    root = tmp_path / "music"
    write_patch(root, {"removed_required_tags": ["comment"]})
    write_patch(root / "艺术家", {"added_required_tags": ["composer"]})
    write_patch(root / "艺术家/Album/CD2", {"added_required_tags": ["comment"]})
    local = LocalRules(root, base_config(root))
    assert scanner.count_files(root, {".mp3"}, on_enter=local.collect) == 0
    assert "comment" not in local.for_directory(root / "Other/Album")["required_tags"]
    artist = local.for_directory(root / "艺术家/Album/CD1")["required_tags"]
    assert "composer" in artist and "comment" not in artist
    assert local.for_directory(root / "艺术家/Album/CD2")["required_tags"][-1] == "comment"
    with pytest.raises(ValueError, match="outside"):
        local.for_directory(tmp_path)


def test_filename_regex_patch_preserves_other_settings_and_parent():
    parent = base_config()
    parent["filename"]["allowed_chars"] = "[]0-9a-zA-Z -()Łóź"
    parent["filename"]["match_template"] = "{tracknumber:02d} - {title}"
    before = deepcopy(parent)
    result = apply_patch(parent, {"filename": {"regex": r"^\d{1,3} - .+$"}})
    assert result["filename"] == {**parent["filename"], "regex": r"^\d{1,3} - .+$"}
    assert parent == before
    assert apply_patch(result, {"filename": {}}) == result
    assert apply_patch(result, {"filename": {"regex": None}})["filename"]["regex"] is None


def test_cached_filename_regex_inheritance_disc_overrides_and_removal(
    tmp_path, library, config_file
):
    mp3 = next(library.rglob("*.mp3"))
    flac = next(library.rglob("*.flac"))
    sibling = library / "Other" / mp3.name
    sibling.parent.mkdir()
    shutil.copyfile(mp3, sibling)
    before = signatures(library)
    config_file.write_text(
        yaml.safe_dump(
            {
                "library_root": str(library),
                "required_tags": [],
                "artwork": {"required": False},
                "filename": {"regex": "^never$", "allowed_chars": ""},
                "cross_album_rules": {"enabled": False},
            }
        )
    )
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"

    def scan(expected):
        cli.run(config_file, db, reports, show_progress=False)
        issues = json.loads((reports / "report.json").read_text())["issues"]
        assert {i["path"] for i in issues if i["rule"] == "filename_pattern"} == {
            str(path) for path in expected
        }
        with (reports / "issues.csv").open(encoding="utf-8-sig", newline="") as stream:
            assert list(csv.DictReader(stream)) == issues
        html = (reports / "index.html").read_text()
        assert html.count("filename_pattern") == len(expected)

    scan([mp3, flac, sibling])
    artist = mp3.parent.parent.parent
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        artist_patch = write_patch(artist, {"filename": {"regex": r"^\d{2} - Łódź$"}})
        scan([sibling])
        disc_patch = write_patch(flac.parent, {"filename": {"regex": r"^\d{3} - .+$"}})
        scan([flac, sibling])
        write_patch(flac.parent, {"filename": {"regex": None}})
        scan([sibling])
        write_patch(artist, {"filename": {"regex": "^different$"}})
        scan([mp3, sibling])
        disc_patch.unlink()
        scan([mp3, flac, sibling])
        artist_patch.unlink()
        scan([mp3, flac, sibling])
    assert signatures(library) == before


def test_album_rules_rejected_in_collapsed_disc_folder(tmp_path):
    root = tmp_path / "music"
    path = write_patch(root / "Album/Disc-02", {"album_rules": {"consistent_album": False}})
    local = LocalRules(root, base_config(root))
    with pytest.raises(ValueError, match="album directory") as error:
        scanner.count_files(root, {".mp3"}, on_enter=local.collect)
    assert str(path) in str(error.value)


def test_album_scope_uses_configured_disc_pattern(tmp_path):
    root = tmp_path / "music"
    write_patch(root / "Album/Part_2", {"album_rules": {"consistent_album": False}})
    config = base_config(root)
    config["disc_folder_pattern"] = r"Part_(?P<disc>\d+)"
    local = LocalRules(root, config)
    with pytest.raises(ValueError, match="album directory"):
        scanner.count_files(root, {".mp3"}, on_enter=local.collect)


@pytest.mark.parametrize("kind", ["symlink", "broken_symlink", "directory", "fifo"])
def test_nonregular_local_configs_rejected_before_outputs(tmp_path, library, config_file, kind):
    path = library / "rules.yaml"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to(config_file if kind == "symlink" else tmp_path / "missing")
    with pytest.raises(ValueError, match="regular file"):
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports")
    assert not (tmp_path / "cache.sqlite").exists()
    assert not (tmp_path / "cache.sqlite.lock").exists()
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    "contents",
    [
        "artwork: {required: true, required: false}",
        "added_required_tags: [",
        "",
        "{}\nunknown: true",
        "added_required_tags: [Title]",
        "filename: {regex: '['}",
        "filename: {regex: true}",
        "filename: {regex: '.*', regex: null}",
    ],
)
def test_invalid_yaml_reports_source_before_outputs(tmp_path, library, config_file, contents):
    local_path = library / "rules.yaml"
    local_path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports")
    assert str(local_path) in str(error.value)
    assert not (tmp_path / "cache.sqlite").exists()
    assert not (tmp_path / "reports").exists()


def test_cached_local_edits_removal_reports_and_read_only(tmp_path, library, config_file):
    config = {
        "library_root": str(library),
        "required_tags": [],
        "artwork": {"required": False},
        "filename": {"regex": None, "allowed_chars": ""},
        "cross_album_rules": {"enabled": False},
        "custom_rules": [rule()],
    }
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    album = next(library.rglob("*.mp3")).parent.parent
    source_before = signatures(library)

    def scan():
        cli.run(config_file, db, reports, show_progress=False)
        found = json.loads((reports / "report.json").read_text())["issues"]
        with (reports / "issues.csv").open(encoding="utf-8-sig", newline="") as stream:
            assert list(csv.DictReader(stream)) == found
        html = (reports / "index.html").read_text()
        assert html.count("<tr>") - html.count("<thead>") == len(found)
        return found

    assert [i["rule"] for i in scan()] == ["credit", "credit"]
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        root_patch = write_patch(library, {"removed_custom_rules": ["credit"]})
        assert scan() == []
        write_patch(album, {"added_required_tags": ["lyricist"]})
        assert [i["rule"] for i in scan()] == ["missing_lyricist", "missing_lyricist"]
        write_patch(
            album / "CD2",
            {
                "removed_required_tags": ["lyricist"],
                "added_custom_rules": [rule("html", "<unsafe & text>")],
            },
        )
        found = scan()
        assert {i["rule"] for i in found} == {"missing_lyricist", "html"}
        assert "&lt;unsafe &amp; text&gt;" in (reports / "index.html").read_text()
        (album / "CD2/rules.yaml").unlink()
        (album / "rules.yaml").unlink()
        root_patch.unlink()
        assert [i["rule"] for i in scan()] == ["credit", "credit"]
    assert signatures(library) == source_before
    catalog = json.loads((reports / "catalog.json").read_text())["albums"]
    assert len(catalog) == 1 and catalog[0]["total_tracks"] == 2


def test_disc_overrides_preserve_album_wide_comparisons(tmp_path, library, config_file):
    album = next(library.rglob("*.mp3")).parent.parent
    # A different filename in CD2 still represents a duplicate tagged disc/track.
    duplicate = album / "CD2/02 - Duplicate.mp3"
    shutil.copyfile(next(library.rglob("*.mp3")), duplicate)
    write_patch(album / "CD2", {"removed_required_tags": ["comment"]})
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports, show_progress=False)
    found = json.loads((reports / "report.json").read_text())["issues"]
    assert any(i["rule"] == "duplicate_track" for i in found)
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("audio reread")):
        write_patch(album, {"album_rules": {"unique_disc_track": False}})
        cli.run(config_file, db, reports, show_progress=False)
    found = json.loads((reports / "report.json").read_text())["issues"]
    assert not any(i["rule"] == "duplicate_track" for i in found)


@pytest.mark.parametrize("change", ["edit", "add", "delete", "remove_directory", "late_edit"])
def test_detected_changes_roll_back_and_preserve_reports(tmp_path, library, config_file, change):
    directory = library / "empty"
    path = write_patch(directory, {}) if change != "add" else directory / "rules.yaml"
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports, show_progress=False)
    current = (reports / "current").readlink()
    real_scan = scanner.scan

    def mutate():
        if change in ("edit", "late_edit", "add"):
            write_patch(directory, {"added_required_tags": ["composer"]})
        else:
            path.unlink()
            if change == "remove_directory":
                directory.rmdir()

    def changing_scan(*args, **kwargs):
        if change != "late_edit":
            mutate()
        result = real_scan(*args, **kwargs)
        if change == "late_edit":
            mutate()
        return result

    with patch.object(scanner, "scan", changing_scan), pytest.raises(ValueError, match="changed"):
        cli.run(config_file, db, reports, full=True, show_progress=False)
    assert (reports / "current").readlink() == current
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1


def test_main_config_inside_library_is_not_reapplied_as_patch(tmp_path, library):
    config = library / "rules.yaml"
    config.write_text(yaml.safe_dump({"library_root": str(library)}), encoding="utf-8")
    assert (
        cli.run(config, tmp_path / "cache.sqlite", tmp_path / "reports", show_progress=False) == 0
    )


def test_track_artwork_overrides_and_complete_album_consistency():
    config = base_config()
    config["custom_rules"] = []
    config["required_tags"] = []
    config["filename"]["allowed_chars"] = ""
    rows = [
        {
            "path": f"/music/Album/CD{disc}/01 - Song.flac",
            "album_dir": "/music/Album",
            "metadata": {
                "tags": {"album": f"Album {disc}", "tracknumber": "1"},
                "artwork": [{"sha256": "same", "width": 100, "height": 100}],
            },
            "disc_from_path": disc,
        }
        for disc in (1, 2)
    ]
    optional_art = apply_patch(config, {"artwork": {"required": False}})
    no_dimensions = apply_patch(
        optional_art,
        {
            "artwork": {"min_width": None, "min_height": None},
        },
    )
    found = validate(
        iter(rows),
        config,
        track_config=lambda row: optional_art if row["disc_from_path"] == 1 else no_dimensions,
    )
    assert sum(i["rule"] == "small_artwork" for i in found) == 2
    assert all("/CD1/" in i["path"] for i in found if i["rule"] == "small_artwork")
    assert sum(i["rule"] == "inconsistent_album" for i in found) == 1
    assert not any(i["rule"] == "duplicate_track" for i in found)


def test_empty_patch_preserves_inherited_configuration():
    config = base_config()
    assert apply_patch(config, {}) == config


def test_reintroduced_custom_rule_and_parent_removal_typo(tmp_path):
    root = tmp_path / "music"
    write_patch(root, {"removed_custom_rules": ["credit"]})
    write_patch(root / "Artist/Album", {"added_custom_rules": [rule()]})
    local = LocalRules(root, base_config(root))
    scanner.count_files(root, {".mp3"}, on_enter=local.collect)
    assert local.for_directory(root / "Artist/Album")["custom_rules"] == [rule()]
    assert local.for_directory(root / "Other")["custom_rules"] == []
    child = write_patch(root / "Other", {"removed_custom_rules": ["credit"]})
    local = LocalRules(root, base_config(root))
    with pytest.raises(ValueError, match="unknown inherited") as error:
        scanner.count_files(root, {".mp3"}, on_enter=local.collect)
    assert str(child) in str(error.value)


@pytest.mark.parametrize("contents", [b"\xff", b"artwork: {required: false}\n"])
def test_unreadable_or_non_utf8_patch_identifies_source(tmp_path, contents):
    root = tmp_path / "music"
    path = write_patch(root, {})
    path.write_bytes(contents)
    local = LocalRules(root, base_config(root))
    if contents == b"\xff":
        with pytest.raises(ValueError) as error:
            scanner.count_files(root, {".mp3"}, on_enter=local.collect)
    else:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with (
                patch("music_validator.local_rules.os.open", side_effect=PermissionError("denied")),
                pytest.raises(ValueError) as error,
            ):
                local.collect(root, fd)
        finally:
            os.close(fd)
    assert str(path) in str(error.value)
