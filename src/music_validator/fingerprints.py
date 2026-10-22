"""Local, full-track Chromaprint extraction from already verified read-only FDs."""

import json
import math
import shutil
import subprocess


class FingerprintError(ValueError):
    """A track could not provide a reliable full-track fingerprint."""


class FingerprintBackend:
    def __init__(self):
        self.executable = shutil.which("fpcalc")
        if self.executable is None:
            raise ValueError(
                "Audio identity requires fpcalc; install libchromaprint-tools "
                "or disable audio_identity_rules.enabled"
            )
        try:
            result = subprocess.run(
                [self.executable, "-version"], capture_output=True, check=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"Cannot run fpcalc: {exc}") from exc
        # Decoding/extraction settings have their own version, independent of tag parsing.
        self.version = "full-raw-algorithm2-v2:" + result.stdout.decode("utf-8", "replace").strip()

    def fingerprint(self, source, extension):
        try:
            result = subprocess.run(
                [
                    self.executable,
                    "-format",
                    extension.lstrip(".").lower(),
                    "-algorithm",
                    "2",
                    "-length",
                    "0",
                    # One chunk uses decoded sample duration, including FLAC
                    # streams with unknown/zero STREAMINFO sample counts.
                    "-chunk",
                    "86400",
                    "-raw",
                    "-json",
                    f"/proc/self/fd/{source.fileno()}",
                ],
                pass_fds=(source.fileno(),),
                check=False,
                capture_output=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FingerprintError(f"Fingerprint decoder failed: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
            raise FingerprintError(f"fpcalc exited with status {result.returncode}: {detail}")
        try:
            if len(result.stdout.strip().splitlines()) > 1:
                raise FingerprintError("Audio exceeds the 24-hour full-track fingerprint limit")
            data = json.loads(result.stdout)
            duration, fingerprint = data["duration"], data["fingerprint"]
            if (
                type(duration) not in (int, float)
                or not math.isfinite(duration)
                or duration <= 0
                or not isinstance(fingerprint, list)
                or any(type(x) is not int or not 0 <= x < 2**32 for x in fingerprint)
            ):
                raise ValueError("invalid duration or fingerprint words")
        except (ValueError, KeyError, TypeError) as exc:
            raise FingerprintError(f"Invalid fpcalc output: {exc}") from exc
        if duration < 10 or len(fingerprint) < 32:
            raise FingerprintError("Too little audio for reliable matching (minimum 10 seconds)")
        if len(set(fingerprint)) < 8:
            raise FingerprintError("Uninformative fingerprint (silence or nearly constant audio)")
        return duration, fingerprint
