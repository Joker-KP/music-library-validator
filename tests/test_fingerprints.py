import json
import subprocess
from types import SimpleNamespace

import pytest

from music_validator.fingerprints import FingerprintBackend, FingerprintError


def test_missing_backend_has_actionable_error(monkeypatch):
    monkeypatch.setattr("music_validator.fingerprints.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="install libchromaprint-tools"):
        FingerprintBackend()


def test_broken_backend_has_actionable_error(monkeypatch):
    monkeypatch.setattr("music_validator.fingerprints.shutil.which", lambda _: "/usr/bin/fpcalc")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "fpcalc")

    monkeypatch.setattr("music_validator.fingerprints.subprocess.run", fail)
    with pytest.raises(ValueError, match="Cannot run fpcalc"):
        FingerprintBackend()


def backend(monkeypatch, result):
    monkeypatch.setattr("music_validator.fingerprints.shutil.which", lambda _: "/usr/bin/fpcalc")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "-version" in command:
            return SimpleNamespace(stdout=b"fpcalc version test", returncode=0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("music_validator.fingerprints.subprocess.run", run)
    return FingerprintBackend(), calls


def test_full_track_read_only_descriptor_not_filename_is_passed(monkeypatch, tmp_path):
    result = SimpleNamespace(
        stdout=json.dumps({"duration": 20, "fingerprint": list(range(100))}).encode(),
        returncode=0,
    )
    tool, calls = backend(monkeypatch, result)
    # This is a source descriptor only; no decoder can reopen a user path/link.
    with (tmp_path / "input").open("wb") as dest:
        dest.write(b"fixture")
    with (tmp_path / "input").open("rb") as source:
        assert tool.fingerprint(source, ".FLAC") == (20, list(range(100)))
        command, kwargs = calls[-1]
        assert command == [
            "/usr/bin/fpcalc",
            "-format",
            "flac",
            "-algorithm",
            "2",
            "-length",
            "0",
            "-chunk",
            "86400",
            "-raw",
            "-json",
            f"/proc/self/fd/{source.fileno()}",
        ]
        assert kwargs["pass_fds"] == (source.fileno(),)
        assert kwargs["timeout"] == 300


@pytest.mark.parametrize(
    "data, message",
    [
        ({"duration": 20, "fingerprint": [0] * 100}, "Uninformative"),
        ({"duration": 5, "fingerprint": list(range(40))}, "Too little"),
        ({"duration": 20, "fingerprint": list(range(10))}, "Too little"),
        ({"duration": 0, "fingerprint": list(range(100))}, "Invalid"),
        ({"duration": float("nan"), "fingerprint": []}, "Invalid"),
        ({"duration": True, "fingerprint": []}, "Invalid"),
        ({"duration": 20, "fingerprint": [-1]}, "Invalid"),
        ({"duration": 20, "fingerprint": [2**32]}, "Invalid"),
        ({"duration": 20, "fingerprint": [True]}, "Invalid"),
        ({}, "Invalid"),
        ([], "Invalid"),
    ],
)
def test_short_silent_or_malformed_fingerprints_are_explicit(monkeypatch, tmp_path, data, message):
    tool, _ = backend(monkeypatch, SimpleNamespace(stdout=json.dumps(data).encode(), returncode=0))
    with (
        (tmp_path / "source").open("w+b") as source,
        pytest.raises(FingerprintError, match=message),
    ):
        tool.fingerprint(source, ".mp3")


@pytest.mark.parametrize(
    "result",
    [
        subprocess.TimeoutExpired("fpcalc", 300),
        OSError("decoder disappeared"),
        SimpleNamespace(stdout=b"{}", stderr=b"decode failed", returncode=3),
        SimpleNamespace(stdout=b"invalid json", returncode=0),
        SimpleNamespace(stdout=b'{"duration":86400}\n{"duration":10}\n', returncode=0),
    ],
)
def test_decode_failures_are_not_successes(monkeypatch, tmp_path, result):
    tool, _ = backend(monkeypatch, result)
    with (tmp_path / "source").open("w+b") as source, pytest.raises(FingerprintError):
        tool.fingerprint(source, ".flac")
