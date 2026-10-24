"""One isolated scan at a time; ephemeral job state, never a second scan engine."""

import copy
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .maintenance import archive_state
from .safety import LockBusyError, regular_output

ACTIVE = {"starting", "running", "cancelling"}


class BusyError(ValueError):
    pass


def timestamp():
    return datetime.now(UTC).isoformat()


def validate_options(value):
    if not isinstance(value, dict) or set(value) - {
        "mode",
        "full_scan",
        "recheck_integrity",
        "recheck_identity",
    }:
        raise ValueError("Expected mode and optional boolean refresh flags")
    result = {
        "mode": "configured",
        "full_scan": False,
        "recheck_integrity": False,
        "recheck_identity": False,
        **value,
    }
    if result["mode"] not in ("configured", "integrity", "identity"):
        raise ValueError("Unknown analysis mode")
    for name in ("full_scan", "recheck_integrity", "recheck_identity"):
        if not isinstance(result[name], bool):
            raise ValueError(f"{name} must be boolean")  # noqa: TRY004 - API validation error
    if result["mode"] == "integrity" and result["recheck_identity"]:
        raise ValueError("Integrity analysis cannot regenerate fingerprints")
    if result["mode"] == "identity" and result["recheck_integrity"]:
        raise ValueError("Identity analysis cannot repeat integrity checks")
    return result


class JobManager:
    def __init__(self, config, db, reports):
        self.config, self.db, self.reports = map(Path, (config, db, reports))
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.thread = None
        self.closing = False
        self.last_reset = None

    def external_busy(self):
        """Read-only advisory probe. The scanner still arbitrates races itself."""
        paths = [Path(str(self.db) + ".lock")]
        paths.extend(
            directory / ".scan.lock"
            for directory in (
                self.reports,
                self.reports / "integrity-report",
                self.reports / "identity-report",
            )
        )
        for path in paths:
            regular_output(path)
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
            finally:
                os.close(fd)
        return False

    def snapshot(self):
        with self.lock:
            active = self.job is not None and self.job["status"] in ACTIVE
            return {
                "job": copy.deepcopy(self.job),
                "external_busy": False if active else self.external_busy(),
                "reset": copy.deepcopy(self.last_reset),
            }

    def fresh_start(self, store):
        with self.lock, store.lock:
            if self.closing or (self.job and self.job["status"] in ACTIVE):
                raise BusyError("Finish or cancel the active analysis before a fresh start")
            try:
                result = archive_state(self.config, self.db, self.reports)
            except LockBusyError as exc:
                raise BusyError("Another scan or fresh start owns the database or reports") from exc
            store.cache.clear()
            self.job = None
            self.last_reset = result
            return copy.deepcopy(result)

    def start(self, options):
        options = validate_options(options)
        with self.lock:
            if self.closing or (self.job and self.job["status"] in ACTIVE):
                raise BusyError("A server scan is already active")
            if self.external_busy():
                raise BusyError("Another CLI or server scan owns the database or reports")
            argv = [
                sys.executable,
                "-m",
                "music_validator.worker",
                "--config",
                str(self.config),
                "--db",
                str(self.db),
                "--reports",
                str(self.reports),
                "--options",
                json.dumps(options),
            ]
            self.job = {
                "id": uuid.uuid4().hex,
                "status": "starting",
                "options": options,
                "started_at": timestamp(),
                "finished_at": None,
                "progress": None,
                "cache_committed": False,
                "reports_published": False,
                "issue_count": None,
                "error": None,
                "logs": [],
                "exit_code": None,
            }
            try:
                self.process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                )
            except OSError as exc:
                self.job.update(status="failed", error=str(exc), finished_at=timestamp())
                raise
            self.job["status"] = "running"
            self.thread = threading.Thread(target=self._watch, daemon=True)
            self.thread.start()
            return copy.deepcopy(self.job)

    def _logs(self, process):
        with process.stderr:
            while line := process.stderr.readline(4096):
                with self.lock:
                    self.job["logs"].append(line.rstrip())
                    del self.job["logs"][:-100]

    def _watch(self):
        process = self.process
        reader = threading.Thread(target=self._logs, args=(process,), daemon=True)
        reader.start()
        try:
            with process.stdout:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self.lock:
                        kind = event.get("type")
                        if kind == "progress":
                            self.job["progress"] = event
                        elif kind == "cache_committed":
                            self.job["cache_committed"] = True
                        elif kind == "reports_published":
                            self.job.update(reports_published=True, issue_count=event["issues"])
                        elif kind == "error":
                            self.job["error"] = event["message"]
            code = process.wait()
            reader.join()
            with self.lock:
                if code == 0 and self.job["reports_published"]:
                    state = "completed_with_findings" if self.job["issue_count"] else "completed"
                elif self.job["status"] == "cancelling" or code == 130:
                    state = "cancelled"
                else:
                    state = "failed"
                    if not self.job["error"]:
                        self.job["error"] = f"Worker exited with code {code}; see job log"
                self.job.update(status=state, exit_code=code, finished_at=timestamp())
        finally:
            # Keep no descriptors or unreaped workers after a job ends.
            if process.poll() is None:
                self._signal(process, signal.SIGKILL)
                process.wait()

    @staticmethod
    def _signal(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def cancel(self, job_id):
        with self.lock:
            if not self.job or self.job["id"] != job_id:
                raise KeyError("Unknown job")
            if self.job["status"] not in ACTIVE:
                raise BusyError("Job has already finished")
            if self.job["status"] == "cancelling":
                return copy.deepcopy(self.job)
            self.job["status"] = "cancelling"
            process = self.process
            self._signal(process, signal.SIGTERM)

            def force_stop():
                if process.poll() is None:
                    self._signal(process, signal.SIGKILL)

            timer = threading.Timer(8, force_stop)
            timer.daemon = True
            timer.start()
            return copy.deepcopy(self.job)

    def close(self):
        with self.lock:
            self.closing = True
            if self.job and self.job["status"] in ACTIVE:
                self.cancel(self.job["id"])
            thread = self.thread
        if thread:
            thread.join(timeout=10)
