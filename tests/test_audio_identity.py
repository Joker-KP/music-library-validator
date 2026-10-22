import random

import pytest

from music_validator.audio_identity import compare, validate_audio_identity
from music_validator.database import AudioIndex, connect

MATCHING = {
    "similarity_threshold": 0.95,
    "duration_tolerance_seconds": 2.0,
    "minimum_coverage": 0.90,
}


def recording(seed=1, length=100):
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(length)]


def track(path, fingerprint=None, duration=20.0, **tags):
    return {
        "path": path,
        "album_dir": "/music/Album",
        "duration": duration,
        "fingerprint": recording() if fingerprint is None else fingerprint,
        "metadata": {"tags": tags},
    }


def test_lossy_fingerprint_differences_alignment_duration_and_coverage():
    original = recording()
    a = track("a", original)
    b = track("b", [value ^ 3 for value in original])
    assert compare(a, b, MATCHING).similarity == 1.0
    b["fingerprint"] = recording(9, 5) + b["fingerprint"]
    assert compare(a, b, MATCHING).coverage == pytest.approx(100 / 105)
    assert compare(a, b, {**MATCHING, "minimum_coverage": 0.99}) is None
    b["duration"] = 22.1
    assert compare(a, b, MATCHING) is None
    assert compare(a, track("c", recording(2)), MATCHING) is None


def test_threshold_is_configurable_and_shared_introduction_is_insufficient():
    original = recording()
    modified = original[:90] + recording(3, 10)
    a, b = track("a", original), track("b", modified)
    assert compare(a, b, MATCHING) is None
    assert compare(a, b, {**MATCHING, "similarity_threshold": 0.9}) is not None
    assert compare(a, track("b", original[:20] + recording(5, 80)), MATCHING) is None


def check(tmp_path, rows, **overrides):
    con = connect(tmp_path / "cache.sqlite")
    try:
        options = {"fields": ["title", "artist"], "severity": "warning", "matching": MATCHING}
        options.update(overrides)
        index = AudioIndex(con, iter(rows), options["fields"])
        return list(validate_audio_identity(index, options))
    finally:
        con.close()


def test_audio_identity_compares_missing_and_different_tags_even_within_one_album(tmp_path):
    rows = [
        track("/music/a.mp3", title="Łódź", artist="First"),
        track("/music/b.flac", title="<Wrong & title>"),
        track("/music/c.flac", recording(3), title="Unrelated"),
    ]
    found = check(tmp_path, rows)
    assert len(found) == 4
    assert {item["rule"] for item in found} == {"audio_identity_title", "audio_identity_artist"}
    assert all("Audio group " in item["message"] for item in found)
    assert all("similarity >= 1.0000; coverage >= 1.0000" in item["message"] for item in found)
    assert {item["path"] for item in found} == {rows[0]["path"], rows[1]["path"]}


def test_field_selection_whitespace_missing_values_and_severity(tmp_path):
    rows = [track("a", title=" Same ", composer="A"), track("b", title="Same", composer="B")]
    assert check(tmp_path, rows) == []
    found = check(tmp_path, rows, fields=["composer"], severity="info")
    assert len(found) == 2 and all(item["severity"] == "info" for item in found)
    assert check(tmp_path, rows, fields=[]) == []


def test_complete_link_groups_do_not_merge_similarity_chains(tmp_path):
    fp = recording()
    a = track("a", fp, title="A")
    b = track("b", fp[:5] + [x ^ 7 for x in fp[5:10]] + fp[10:], title="B")
    c = track("c", [x ^ 7 for x in fp[:10]] + fp[10:], title="C")
    assert compare(a, b, MATCHING) is not None
    assert compare(b, c, MATCHING) is not None
    assert compare(a, c, MATCHING) is None
    found = check(tmp_path, [c, b, a])
    assert {item["path"] for item in found} == {"a", "b"}
    assert check(tmp_path, [a, b, c]) == found


def test_index_finds_noisy_audio_without_using_tags_or_all_pairs(tmp_path):
    con = connect(tmp_path / "cache.sqlite")
    try:
        fp = recording(length=80)
        rows = [track("a", fp), track("b", [x ^ 3 for x in fp]), track("c", recording(10))]
        index = AudioIndex(con, iter(rows), ["title"])
        assert list(index.candidate_paths(index.get("b"), MATCHING)) == ["a"]
        assert list(index.candidate_paths(index.get("c"), MATCHING)) == []
    finally:
        con.close()


@pytest.mark.parametrize("shift", [0, 1, 2, 3, 4, 5])
def test_index_retains_accepted_matches_across_sampling_phases(tmp_path, shift):
    original = recording(length=100)
    modified = recording(6, shift) + [word ^ 3 for word in original]
    modified[-4:] = [word ^ 4 for word in modified[-4:]]
    left, right = track("a", original), track("b", modified)
    assert compare(left, right, MATCHING) is not None
    con = connect(tmp_path / "cache.sqlite")
    try:
        index = AudioIndex(con, iter([left, right]), ["title"])
        assert list(index.candidate_paths(index.get("b"), MATCHING)) == ["a"]
    finally:
        con.close()
