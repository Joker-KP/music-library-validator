"""Read-only, full-stream checks, independent of metadata and fingerprints."""

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from threading import Thread


@dataclass
class CheckResult:
    # Failed means a media error; incomplete means no integrity verdict is possible.
    status: str
    findings: list[dict] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def finding(rule, message, severity="warning"):
    return {"rule": rule, "message": message, "severity": severity}


def incomplete(check, message):
    return CheckResult("incomplete", [finding("audio_integrity_incomplete", f"{check}: {message}")])


def selected_checks(options, extension):
    if not options["enabled"]:
        return ()
    if extension.lower() == ".flac":
        return ("flac",) if options["flac"]["native_test"] else ()
    if extension.lower() == ".mp3":
        return tuple(
            name
            for name, enabled in (
                ("mp3val", options["mp3"]["structure_check"]),
                ("ffmpeg", options["mp3"]["decode_check"]),
            )
            if enabled
        )
    return ()


def _command_output(argv, fd, timeout):
    """Drain output continuously, retaining at most 128 KiB, with a deadline."""
    first = bytearray()
    tail = bytearray()
    total = 0
    limit = 65536
    with subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        pass_fds=(fd,),
        env={**os.environ, "LC_ALL": "C"},
    ) as process:

        def drain():
            nonlocal total
            while chunk := process.stdout.read(8192):
                total += len(chunk)
                if len(first) < limit:
                    first.extend(chunk[: limit - len(first)])
                tail.extend(chunk)
                del tail[:-limit]

        reader = Thread(target=drain, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join()
    truncated = total > 2 * limit
    output = bytes(first) if total <= limit else bytes(first) + bytes(tail[-(total - limit) :])
    return process.returncode, output.decode("utf-8", "replace"), truncated


def interpret(check, code, output, truncated=False):
    """Parse diagnostics, including tools that exit zero after media warnings."""
    if code < 0:
        return incomplete(check, f"tool terminated by signal {-code}")
    excerpt = output.strip()[:4000]
    lower = output.lower()
    if any(
        text in lower
        for text in (
            "permission denied",
            "input/output error",
            "cannot open",
            "can't open",
            "not enough memory",
            "cannot allocate memory",
            "out of memory",
            "resource temporarily unavailable",
            "no space left on device",
            "too many open files",
        )
    ):
        return incomplete(check, excerpt or f"tool exited with status {code}")
    findings = []
    if check == "mp3val":
        for line in output.splitlines():
            if not line.startswith(("WARNING:", "ERROR:")):
                continue
            if "no supported tags" in line.lower():
                continue  # Tag presence is handled by metadata rules.
            severity = "error" if line.startswith("ERROR:") else "warning"
            if severity == "error" and not any(
                text in line.lower() for text in ("unknown file format", "invalid file format")
            ):
                return incomplete(check, excerpt)
            rule = "mp3_structure_error" if severity == "error" else "mp3_structure_warning"
            item = finding(rule, line[:2000], severity)
            if item not in findings:
                findings.append(item)
        # MP3val can return success for invalid data. Require its completion marker.
        if (code or "Done!" not in output) and not any(
            item["severity"] == "error" for item in findings
        ):
            checked = incomplete(check, excerpt or f"tool exited with status {code}")
            checked.findings[:0] = findings
            return checked
    elif check == "flac":
        if "md5" in lower and ("mismatch" in lower or "did not match" in lower):
            findings.append(finding("flac_checksum_mismatch", excerpt, "error"))
        elif code and any(
            text in lower
            for text in (
                "during decoding",
                "while decoding",
                "error decoding",
                "lost_sync",
                "crc",
                "unparseable",
                "end of stream",
                "not a flac",
                "number of samples",
            )
        ):
            findings.append(finding("audio_decode_error", f"flac: {excerpt}", "error"))
        elif code:
            return incomplete(check, excerpt or f"tool exited with status {code}")
        if "md5" in lower and any(
            text in lower for text in ("unset", "not set", "not checking", "cannot check")
        ):
            findings.append(
                finding(
                    "flac_checksum_unavailable",
                    "FLAC has no stored audio MD5; stream checks ran, but audio MD5 verification is unavailable.",
                )
            )
        elif not code and "warning" in lower:
            findings.append(finding("audio_integrity_warning", f"flac: {excerpt}"))
    else:
        if code:
            if any(
                text in lower
                for text in (
                    "invalid data",
                    "header missing",
                    "error submitting packet",
                    "error while decoding",
                    "error processing packet",
                    "invalid frame",
                    "crc mismatch",
                    "failed to read frame size",
                    "failed to find two consecutive mpeg audio frames",
                )
            ):
                findings.append(finding("audio_decode_error", f"ffmpeg: {excerpt}", "error"))
            else:
                return incomplete(check, excerpt or f"tool exited with status {code}")
        else:
            warnings = [
                line
                for line in output.splitlines()
                if line.strip() and "estimating duration from bitrate" not in line.lower()
            ]
            if warnings:
                findings.append(
                    finding("audio_integrity_warning", "ffmpeg: " + "\n".join(warnings)[:4000])
                )
    if truncated:
        findings.extend(
            incomplete(check, "diagnostics exceeded 128 KiB; check must be repeated").findings
        )
    if truncated:
        status = "incomplete"
    elif any(item["severity"] == "error" for item in findings):
        status = "failed"
    else:
        status = "warning" if findings else "passed"
    return CheckResult(status, findings)


class IntegrityBackend:
    def __init__(self, options, extensions):
        self.timeout = options["timeout_seconds"]
        self.checks = {ext: selected_checks(options, ext) for ext in extensions}
        self.executables = {}
        self.versions = {}
        for check in sorted({check for checks in self.checks.values() for check in checks}):
            executable = shutil.which(check)
            if executable is None:
                raise ValueError(
                    f"Audio integrity requires {check}; install flac, mp3val and ffmpeg or disable the corresponding check"
                )
            try:
                probe = subprocess.run(
                    [
                        executable,
                        {"mp3val": "-v", "ffmpeg": "-version", "flac": "--version"}[check],
                    ],
                    capture_output=True,
                    check=True,
                    timeout=10,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f"Cannot run integrity tool {check}: {exc}") from exc
            version = (probe.stdout + probe.stderr).decode("utf-8", "replace").strip()
            if not version:
                raise ValueError(f"Cannot identify integrity tool version: {check}")
            self.executables[check] = executable
            # Per-tool profile, independent of enabled checks and tag rules.
            self.versions[check] = f"integrity-v1:{check}:{version}"

    def check(self, check, source):
        fd = source.fileno()
        path = f"/proc/self/fd/{fd}"
        executable = self.executables[check]
        if check == "flac":
            argv = [executable, "--test", "--silent", "--", path]
        elif check == "mp3val":
            argv = [executable, path]  # Never pass -f (repair).
        else:
            argv = [
                executable,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-xerror",
                "-f",
                "mp3",
                "-i",
                path,
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ]
        try:
            if os.fstat(fd).st_size == 0:
                rule = "mp3_structure_error" if check == "mp3val" else "audio_decode_error"
                return CheckResult(
                    "failed", [finding(rule, f"{check}: Audio file is empty", "error")]
                )
            original_path = os.readlink(path)
            code, output, truncated = _command_output(argv, fd, self.timeout)
            # Descriptor paths are implementation details, not useful diagnostics.
            output = output.replace(original_path, "<audio>")
            output = re.sub(r"/proc/self/fd/\d+", "<audio>", output)
            if check == "flac":
                output = re.sub(rf"(?m)^{fd}:", "<audio>:", output)
            output = re.sub(r"0x[0-9a-fA-F]+(?=\])", "<address>", output)
            return interpret(check, code, output, truncated)
        except subprocess.TimeoutExpired:
            return incomplete(
                check, f"timed out after {self.timeout:g} seconds; no integrity verdict"
            )
        except OSError as exc:
            return incomplete(check, str(exc))
