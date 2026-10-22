import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from music_validator import cli, integrity
from music_validator.config import DEFAULTS, load_config
from music_validator.integrity import IntegrityBackend, interpret, selected_checks


@pytest.mark.parametrize(
    "options",
    [
        None,
        [],
        {"unknown": True},
        {"enabled": "true"},
        {"enabled": 1},
        {"flac": None},
        {"mp3": []},
        {"flac": {"native_test": 0}},
        {"mp3": {"decode_check": "false"}},
        {"mp3": {"structure_check": None}},
        {"mp3": {"repair": True}},
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "300"},
    ],
)
def test_invalid_config_before_outputs(tmp_path, config_file, options):
    config = yaml.safe_load(config_file.read_text())
    config["audio_integrity"] = options
    config_file.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        cli.run(config_file, tmp_path / "db.sqlite", tmp_path / "reports")
    assert not (tmp_path / "db.sqlite").exists()
    assert not (tmp_path / "reports").exists()


def test_config_defaults_partial_merge_and_check_selection(config_file):
    assert load_config(config_file)["audio_integrity"] == DEFAULTS["audio_integrity"]
    config = yaml.safe_load(config_file.read_text())
    config["audio_integrity"] = {"enabled": True, "mp3": {"decode_check": False}}
    config_file.write_text(yaml.safe_dump(config))
    options = load_config(config_file)["audio_integrity"]
    assert selected_checks(options, ".MP3") == ("mp3val",)
    assert selected_checks(options, ".flac") == ("flac",)
    assert options["timeout_seconds"] == 300
    options["enabled"] = False
    assert not selected_checks(options, ".mp3")
    assert DEFAULTS["audio_integrity"]["mp3"]["decode_check"] is True


@pytest.mark.parametrize(
    "check,code,output,status,rules",
    [
        ("ffmpeg", 0, "", "passed", []),
        ("ffmpeg", 0, "Estimating duration from bitrate, this may be inaccurate", "passed", []),
        (
            "ffmpeg",
            1,
            "Header missing: Invalid data found when processing input",
            "failed",
            ["audio_decode_error"],
        ),
        ("ffmpeg", 1, "Unknown decoder", "incomplete", ["audio_integrity_incomplete"]),
        (
            "ffmpeg",
            1,
            "Error opening input files: Invalid data found when processing input",
            "failed",
            ["audio_decode_error"],
        ),
        (
            "ffmpeg",
            1,
            "Error opening input: Permission denied",
            "incomplete",
            ["audio_integrity_incomplete"],
        ),
        ("ffmpeg", 0, "invalid new backstep -1", "warning", ["audio_integrity_warning"]),
        (
            "mp3val",
            0,
            'WARNING: "<audio>": MPEG stream error, resynchronized successfully\nDone!',
            "warning",
            ["mp3_structure_warning"],
        ),
        (
            "mp3val",
            0,
            'WARNING: "<audio>": It seems that file is truncated or there is garbage at the end of the file\nDone!',
            "warning",
            ["mp3_structure_warning"],
        ),
        ("mp3val", 0, 'WARNING: "<audio>": No supported tags in the file\nDone!', "passed", []),
        ("mp3val", 0, 'ERROR: "<audio>": Invalid file format', "failed", ["mp3_structure_error"]),
        ("mp3val", 0, "", "incomplete", ["audio_integrity_incomplete"]),
        (
            "mp3val",
            1,
            "WARNING: unexpected warning\nDone!",
            "incomplete",
            ["mp3_structure_warning", "audio_integrity_incomplete"],
        ),
        ("flac", 1, "ERROR, MD5 signature mismatch", "failed", ["flac_checksum_mismatch"]),
        ("flac", 1, "ERROR: FRAME_CRC_MISMATCH", "failed", ["audio_decode_error"]),
        (
            "flac",
            0,
            "WARNING, cannot check MD5 signature since it was unset in the STREAMINFO",
            "warning",
            ["flac_checksum_unavailable"],
        ),
        ("flac", 0, "WARNING: unusual metadata", "warning", ["audio_integrity_warning"]),
        ("flac", 1, "unexpected failure", "incomplete", ["audio_integrity_incomplete"]),
        ("flac", 1, "ERROR: unrecognized option", "incomplete", ["audio_integrity_incomplete"]),
        ("mp3val", 1, "ERROR: unexpected tool error", "incomplete", ["audio_integrity_incomplete"]),
        ("flac", 1, "ERROR: Input/output error", "incomplete", ["audio_integrity_incomplete"]),
        ("mp3val", 0, "Cannot open input file", "incomplete", ["audio_integrity_incomplete"]),
        ("mp3val", 1, "ERROR: Not enough memory", "incomplete", ["audio_integrity_incomplete"]),
        ("ffmpeg", -11, "", "incomplete", ["audio_integrity_incomplete"]),
    ],
)
def test_diagnostic_classification(check, code, output, status, rules):
    result = interpret(check, code, output)
    assert result.status == status
    assert [item["rule"] for item in result.findings] == rules
    if status != "failed":
        assert all(item["severity"] != "error" for item in result.findings)


def test_truncated_diagnostics_are_incomplete():
    assert interpret("ffmpeg", 0, "", truncated=True).status == "incomplete"
    checked = interpret("ffmpeg", 1, "Invalid data", truncated=True)
    assert checked.status == "incomplete"
    assert [item["rule"] for item in checked.findings] == [
        "audio_decode_error",
        "audio_integrity_incomplete",
    ]


def test_process_timeout_and_bounded_capture():
    with Path(__file__).open("rb") as source:
        with pytest.raises(subprocess.TimeoutExpired):
            integrity._command_output(
                [sys.executable, "-c", "import time; time.sleep(10)"], source.fileno(), 0.05
            )
        code, output, truncated = integrity._command_output(
            [sys.executable, "-c", "print('A'*200000)"],
            source.fileno(),
            5,
        )
    assert code == 0 and truncated and len(output) == 131072


def test_missing_tool_preflight(tmp_path, config_file, monkeypatch):
    config = yaml.safe_load(config_file.read_text())
    config["audio_integrity"] = {"enabled": True}
    config_file.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(integrity.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Audio integrity requires"):
        cli.run(config_file, tmp_path / "db.sqlite", tmp_path / "reports")
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "db.sqlite").exists()


def test_force_recheck_requires_enabled_checks(tmp_path, config_file, capsys):
    assert (
        cli.main(
            [
                "scan",
                "--config",
                str(config_file),
                "--db",
                str(tmp_path / "db.sqlite"),
                "--reports",
                str(tmp_path / "reports"),
                "--recheck-integrity",
            ]
        )
        == 2
    )
    assert "requires audio_integrity.enabled" in capsys.readouterr().err
    assert not (tmp_path / "db.sqlite").exists()
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("broken", ["empty_version", "cannot_run"])
def test_broken_tool_version_preflight(monkeypatch, broken):
    options = deepcopy(DEFAULTS["audio_integrity"])
    options["enabled"] = True
    monkeypatch.setattr(integrity.shutil, "which", lambda name: name)

    def probe(*args, **kwargs):
        if broken == "cannot_run":
            raise OSError("cannot execute")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(integrity.subprocess, "run", probe)
    with pytest.raises(ValueError, match="integrity tool"):
        IntegrityBackend(options, {".flac"})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_only_selected_tools_required_and_timeout_reported(monkeypatch):
    options = deepcopy(DEFAULTS["audio_integrity"])
    options.update(enabled=True, mp3={"structure_check": False, "decode_check": True})
    real_which = shutil.which
    monkeypatch.setattr(
        integrity.shutil, "which", lambda name: real_which(name) if name == "ffmpeg" else None
    )
    backend = IntegrityBackend(options, {".mp3"})
    assert set(backend.versions) == {"ffmpeg"}
    monkeypatch.setattr(
        integrity,
        "_command_output",
        lambda *args: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 1)),
    )
    with Path(__file__).open("rb") as source:
        checked = backend.check("ffmpeg", source)
    assert checked.status == "incomplete" and "timed out" in checked.findings[0]["message"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_subprocess_arguments_are_read_only(monkeypatch):
    options = deepcopy(DEFAULTS["audio_integrity"])
    options["enabled"] = True
    monkeypatch.setattr(integrity.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        integrity.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b"test-version", b""),
    )
    backend = IntegrityBackend(options, {".mp3", ".flac"})
    commands = []

    def command(argv, fd, timeout):
        commands.append(argv)
        assert os.path.samefile(f"/proc/self/fd/{fd}", __file__)
        assert timeout == 300
        return 0, "Done!" if argv[0].endswith("mp3val") else "", False

    monkeypatch.setattr(integrity, "_command_output", command)
    with Path(__file__).open("rb") as source:
        for check in backend.versions:
            backend.check(check, source)
    mp3val = next(argv for argv in commands if argv[0].endswith("mp3val"))
    assert len(mp3val) == 2 and mp3val[1].startswith("/proc/self/fd/")
    flac = next(argv for argv in commands if argv[0].endswith("flac"))
    assert flac[1:4] == ["--test", "--silent", "--"]
    ffmpeg = next(argv for argv in commands if argv[0].endswith("ffmpeg"))
    assert "-xerror" in ffmpeg and "-nostdin" in ffmpeg
    assert ffmpeg[ffmpeg.index("-i") - 2 : ffmpeg.index("-i")] == ["-f", "mp3"]
    assert ffmpeg[-5:] == ["-map", "0:a:0", "-f", "null", "-"]
