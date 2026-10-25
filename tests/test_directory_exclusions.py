import os
from pathlib import Path
from unittest.mock import patch

import pytest

from music_validator import scanner


@pytest.mark.parametrize(
    "patterns,expected",
    [
        ([], {"keep.mp3", "hidden.flac", "similar.mp3", "#recycle.mp3"}),
        (["#recycle"], {"keep.mp3", "#recycle.mp3"}),
        ([r"(^|/)#recycle(/|$)"], {"keep.mp3", "similar.mp3", "#recycle.mp3"}),
        ([r"/Artist/Album/", "#recycle-copy"], {"#recycle.mp3"}),
    ],
)
def test_directory_patterns_search_full_paths_in_both_passes(tmp_path, patterns, expected):
    root = tmp_path / "music"
    for relative in (
        "Artist/Album/CD1/keep.mp3",
        "Artist/Album/#recycle/hidden.flac",
        "Artist/Album/#recycle-copy/similar.mp3",
        "#recycle.mp3",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    paths = list(scanner.discover(root, {".mp3", ".flac"}, exclude_directory_patterns=patterns))
    assert {path.name for path, *_ in paths} == expected
    increments = []
    assert scanner.count_files(
        root, {".mp3", ".flac"}, increments.append, exclude_directory_patterns=patterns
    ) == len(expected)
    assert increments == [1] * len(expected)


def test_excluded_directory_is_not_opened_or_counted_in_folder_sizes(tmp_path):
    root = tmp_path / "music"
    album = root / "Łódź"
    excluded = album / "#recycle"
    excluded.mkdir(parents=True)
    (excluded / "hidden.mp3").write_bytes(b"hidden")
    (excluded / "cover.jpg").write_bytes(b"large sidecar")
    (album / "01.mp3").write_bytes(b"audio")
    (album / "cover.jpg").write_bytes(b"art")
    real_open = os.open
    entered, sizes = [], {}

    def guarded_open(path, *args, **kwargs):
        assert "#recycle" not in str(path), "Excluded directories must not be opened"
        return real_open(path, *args, **kwargs)

    with patch.object(scanner.os, "open", side_effect=guarded_open):
        paths = list(
            scanner.discover(
                root,
                {".mp3"},
                on_enter=lambda path, fd: entered.append(path),
                on_directory=lambda path, size: sizes.update({path: size}),
                exclude_directory_patterns=["#recycle"],
            )
        )
    assert [path for path, *_ in paths] == [album / "01.mp3"]
    assert excluded not in entered and excluded not in sizes
    assert sizes[album] == sizes[root] == len(b"audioart")


def test_relative_root_patterns_use_absolute_path(tmp_path, monkeypatch):
    root = tmp_path / "music" / "藝術家"
    root.mkdir(parents=True)
    (root / "01.mp3").write_bytes(b"synthetic")
    monkeypatch.chdir(tmp_path)
    assert (
        scanner.count_files(
            Path("music"), {".mp3"}, exclude_directory_patterns=[rf"^{tmp_path}/music/藝術家$"]
        )
        == 0
    )


def test_matching_library_root_skips_whole_tree_without_opening_it(tmp_path):
    root = tmp_path / "#recycle"
    root.mkdir()
    with patch.object(scanner.os, "open", side_effect=AssertionError("excluded root opened")):
        assert scanner.count_files(root, {".mp3"}, exclude_directory_patterns=["#recycle"]) == 0
