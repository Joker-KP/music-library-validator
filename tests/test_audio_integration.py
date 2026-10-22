import csv
import fcntl
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import wave
from array import array
from io import BytesIO
from unittest.mock import patch

import pytest
import yaml
from conftest import signatures

from music_validator import cli, database, scanner
from music_validator.fingerprints import FingerprintBackend, FingerprintError


class FakeBackend:
    version = "fake-full-track-v1"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def fingerprint(self, source, extension):
        assert fcntl.fcntl(source.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        self.calls += 1
        if self.fail:
            raise FingerprintError("simulated decoding failure")
        rng = random.Random(42)
        return 20.0, [rng.getrandbits(32) for _ in range(100)]


@pytest.fixture
def audio_setup(tmp_path, library, config_file, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "FingerprintBackend", lambda: backend)
    config = yaml.safe_load(config_file.read_text())
    config.update(
        {
            "required_tags": [],
            "artwork": {"required": False},
            "filename": {"regex": None, "allowed_chars": None},
            "cross_album_rules": {"enabled": False},
            "audio_identity_rules": {"enabled": True, "fields": ["title"]},
        }
    )
    original_reader = scanner.read_metadata

    def reader(path, **kwargs):
        metadata = original_reader(path, **kwargs)
        metadata["tags"]["title"] = "<First & title>" if path.suffix == ".mp3" else "Second title"
        return metadata

    monkeypatch.setattr(scanner, "read_metadata", reader)

    def scan(full=False):
        config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports", full)
        return json.loads((tmp_path / "reports/report.json").read_text())["issues"]

    return backend, config, scan


def test_audio_cache_config_revalidation_reports_and_read_only(tmp_path, library, audio_setup):
    backend, config, scan = audio_setup
    before = signatures(library)
    found = scan()
    assert len(found) == 2 and all(i["rule"] == "audio_identity_title" for i in found)
    assert backend.calls == 2
    with (tmp_path / "reports/issues.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == found
    document = (tmp_path / "reports/index.html").read_text()
    assert "&lt;First &amp; title&gt;" in document and "<First & title>" not in document
    assert document.count("audio_identity_title") == 2
    catalog = json.loads((tmp_path / "reports/catalog.json").read_text())["albums"]
    assert len(catalog) == 1 and catalog[0]["number_of_issues"] == 2
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert scan() == found
        config["audio_identity_rules"]["severity"] = "info"
        assert all(i["severity"] == "info" for i in scan())
        config["audio_identity_rules"]["matching"] = {
            "similarity_threshold": 1,
            "minimum_coverage": 1,
            "duration_tolerance_seconds": 0,
        }
        assert len(scan()) == 2
        config["audio_identity_rules"]["fields"] = ["composer"]
        assert scan() == []
        config["audio_identity_rules"]["fields"] = []
        assert scan() == []
        config["audio_identity_rules"]["fields"] = ["title"]
        config["audio_identity_rules"]["enabled"] = False
        assert scan() == []
        config["audio_identity_rules"]["enabled"] = True
        assert len(scan()) == 2
    assert backend.calls == 2
    scan(full=True)
    assert backend.calls == 4
    assert signatures(library) == before


def test_enabling_audio_populates_existing_cache_without_metadata_rereads(audio_setup):
    backend, config, scan = audio_setup
    config["audio_identity_rules"]["enabled"] = False
    assert scan() == [] and backend.calls == 0
    config["audio_identity_rules"]["enabled"] = True
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert len(scan()) == 2
    assert backend.calls == 2


def test_counting_precedes_reads_and_second_walk_includes_fingerprints(
    audio_setup, monkeypatch, capsys
):
    backend, _, scan = audio_setup
    original_discover = scanner.discover
    original_reader = scanner.read_metadata
    original_fingerprint = backend.fingerprint
    walks = []

    def discover(*args, **kwargs):
        walks.append("started")
        yield from original_discover(*args, **kwargs)
        walks[-1] = "finished"

    def reader(*args, **kwargs):
        assert walks == ["finished", "started"]
        return original_reader(*args, **kwargs)

    def fingerprint(*args, **kwargs):
        assert walks == ["finished", "started"]
        return original_fingerprint(*args, **kwargs)

    monkeypatch.setattr(scanner, "discover", discover)
    monkeypatch.setattr(scanner, "read_metadata", reader)
    monkeypatch.setattr(backend, "fingerprint", fingerprint)
    assert len(scan()) == 2
    assert walks == ["finished", "finished"] and backend.calls == 2
    output = capsys.readouterr()
    assert "Counting audio files" in output.err
    assert "Analyzing tracks" in output.err and "100% 2/2 files" in output.err
    assert "Matching audio" in output.err and "Publishing reports" in output.err
    assert "Indexed 2 tracks; reread 2" in output.out


def test_matching_settings_regroup_cached_fingerprints(audio_setup, monkeypatch):
    backend, config, scan = audio_setup
    original = backend.fingerprint

    def noisy(source, extension):
        duration, fp = original(source, extension)
        if extension == ".flac":
            rng = random.Random(900)
            fp = (
                [rng.getrandbits(32) for _ in range(5)]
                + fp[:90]
                + [rng.getrandbits(32) for _ in range(10)]
            )
            duration += 2.1
        return duration, fp

    monkeypatch.setattr(backend, "fingerprint", noisy)
    assert scan() == []
    matching = config["audio_identity_rules"]["matching"] = {}
    with patch.object(backend, "fingerprint", side_effect=AssertionError("audio reread")):
        matching["duration_tolerance_seconds"] = 3
        assert scan() == []
        matching["similarity_threshold"] = 0.9
        assert len(scan()) == 2
        matching["minimum_coverage"] = 1
        assert scan() == []
        matching["minimum_coverage"] = 0.9
        assert len(scan()) == 2
    assert backend.calls == 2


def test_audio_cache_prunes_removed_and_changed_tracks(audio_setup, tmp_path):
    _, _, scan = audio_setup
    scan()
    con = database.connect(tmp_path / "cache.sqlite")
    try:
        paths = [row[0] for row in con.execute("SELECT path FROM tracks ORDER BY path")]
        con.execute("DELETE FROM files WHERE path=?", (paths[0],))
        con.execute("UPDATE files SET size=size+1 WHERE path=?", (paths[1],))
        database.prune_audio_fingerprints(con)
        assert con.execute("SELECT count(*) FROM audio_fingerprints").fetchone()[0] == 0
    finally:
        con.rollback()
        con.close()


def test_audio_errors_preserve_tags_retry_and_backend_version_refresh(audio_setup, tmp_path):
    backend, _, scan = audio_setup
    backend.fail = True
    assert {i["rule"] for i in scan()} == {"audio_fingerprint_error"}
    assert backend.calls == 2
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM tracks WHERE error IS NULL").fetchone()[0] == 2
    backend.fail = False
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert len(scan()) == 2
        assert backend.calls == 4
        backend.version = "fake-full-track-v2"
        scan()
        assert backend.calls == 6


def test_audio_validation_failure_rolls_back_cache_and_reports(audio_setup, tmp_path):
    backend, _, scan = audio_setup
    scan()
    current = (tmp_path / "reports/current").readlink()
    backend.version = "new-backend"
    with (
        patch.object(cli, "validate_audio_identity", side_effect=RuntimeError("comparison failed")),
        pytest.raises(RuntimeError, match="comparison failed"),
    ):
        scan()
    assert (tmp_path / "reports/current").readlink() == current
    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        assert con.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
        assert (
            con.execute("SELECT DISTINCT backend_version FROM audio_fingerprints").fetchone()[0]
            == "fake-full-track-v1"
        )


def test_schema1_migration_preserves_cached_tags_and_can_roll_back(audio_setup, tmp_path):
    backend, config, scan = audio_setup
    config["audio_identity_rules"]["enabled"] = False
    scan()
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as con:
        before = con.execute("SELECT * FROM tracks ORDER BY path").fetchall()
        con.execute("DROP TABLE audio_fingerprints")
        con.execute("DROP TABLE audio_integrity")
        con.execute("DROP TABLE files")
        con.execute("ALTER TABLE scans DROP COLUMN mode")
        con.execute("PRAGMA user_version=1")
    con = database.connect(path)
    con.rollback()
    con.close()
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert con.execute("SELECT * FROM tracks ORDER BY path").fetchall() == before
    config["audio_identity_rules"]["enabled"] = True
    with patch.object(scanner, "read_metadata", side_effect=AssertionError("metadata reread")):
        assert len(scan()) == 2 and backend.calls == 2
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        assert con.execute("SELECT metadata_json FROM tracks ORDER BY path").fetchall() == [
            (r[4],) for r in before
        ]


def test_missing_backend_fails_before_creating_outputs(tmp_path, config_file, monkeypatch):
    config = yaml.safe_load(config_file.read_text())
    config["audio_identity_rules"] = {"enabled": True}
    config_file.write_text(yaml.safe_dump(config))
    monkeypatch.setattr("music_validator.fingerprints.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="fpcalc"):
        cli.run(config_file, tmp_path / "cache.sqlite", tmp_path / "reports")
    assert not (tmp_path / "cache.sqlite").exists()
    assert not (tmp_path / "reports").exists()


def synthetic_wav(seconds=18):
    rng = random.Random(91)
    notes = [rng.sample(range(48, 85), 3) for _ in range(seconds * 4)]
    rate = 44100
    samples = array("h")
    for sample in range(seconds * rate):
        time = sample / rate
        chord = notes[min(int(time * 4), len(notes) - 1)]
        samples.append(
            int(
                sum(math.sin(2 * math.pi * 440 * 2 ** ((note - 69) / 12) * time) for note in chord)
                * 6000
            )
        )
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    return stream.getvalue()


@pytest.mark.skipif(
    not shutil.which("fpcalc") or not shutil.which("ffmpeg"), reason="fpcalc and FFmpeg required"
)
@pytest.mark.parametrize("bitrate", [64, 128, 320])
def test_real_mp3_flac_fingerprints_and_cli(tmp_path, capsys, bitrate):
    library = tmp_path / "music"
    source = synthetic_wav()
    for disc, ext, codec in [(1, "mp3", "libmp3lame"), (2, "flac", "flac")]:
        path = library / "Album" / f"CD{disc}" / f"01 - Song.{ext}"
        path.parent.mkdir(parents=True)
        # Create fresh synthetic fixtures on a seekable output so MP3 carries
        # its encoder-delay/gapless header. Existing source audio is never edited.
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-map_metadata",
                "-1",
                "-metadata",
                f"title={'Łódź' if ext == 'mp3' else '<Wrong & title>'}",
                "-metadata",
                "album=Synthetic",
                "-metadata",
                "artist=Artist",
                "-metadata",
                "track=1",
                "-c:a",
                codec,
                *(["-b:a", f"{bitrate}k"] if ext == "mp3" else []),
                "-f",
                ext,
                str(path),
            ],
            input=source,
            capture_output=True,
            check=True,
        )
    before = signatures(library)
    config_file = tmp_path / "rules.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "library_root": str(library),
                "required_tags": [],
                "artwork": {"required": False},
                "audio_identity_rules": {"enabled": True},
            }
        )
    )
    db, reports = tmp_path / "cache.sqlite", tmp_path / "reports"
    cli.run(config_file, db, reports)
    found = json.loads((reports / "report.json").read_text())["issues"]
    assert len(found) == 2 and all(i["rule"] == "audio_identity_title" for i in found)
    assert "Audio fingerprints: decoded 2; fingerprint errors 0" in capsys.readouterr().out
    with patch.object(
        FingerprintBackend, "fingerprint", side_effect=AssertionError("audio reread")
    ):
        cli.run(config_file, db, reports)
    assert "decoded 0" in capsys.readouterr().out
    assert signatures(library) == before


@pytest.mark.skipif(
    not shutil.which("fpcalc") or not shutil.which("ffmpeg"), reason="fpcalc and FFmpeg required"
)
def test_flac_without_header_duration_uses_decoded_samples(tmp_path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-c:a", "flac", "-f", "flac", "pipe:1"],
        input=synthetic_wav(seconds=12),
        capture_output=True,
        check=True,
    )
    path = tmp_path / "synthetic.flac"
    path.write_bytes(result.stdout)
    backend = FingerprintBackend()
    with path.open("rb") as source:
        duration, fp = backend.fingerprint(source, ".flac")
    assert duration == pytest.approx(12, abs=0.01)
    assert len(fp) >= 32
