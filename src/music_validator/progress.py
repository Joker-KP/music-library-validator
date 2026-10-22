"""Throttled CLI progress on stderr, independent of scanning and validation."""

import shutil
import sys
import time
from contextlib import contextmanager

# Reserve the width of the longest scan phase for every progress label.
LABEL_WIDTH = len("Counting audio files")


def _duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class Progress:
    def __init__(self, stream=None, enabled=True, clock=time.monotonic):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = enabled
        self.clock = clock
        self.terminal = self.stream.isatty()

    @contextmanager
    def phase(self, label, total=None):
        self.label, self.total, self.count = label, total, 0
        self.started = self.clock()
        self.updated = self.started
        self._render(force=True)
        try:
            yield self.advance
        except BaseException:
            self._render(force=True, failed=True)
            raise
        else:
            # Discovery is an estimate; additions/removals must not leave the
            # completed bar short of 100% or misstate the actual file count.
            if self.total is not None:
                self.total = self.count
            self._render(force=True, finished=True)

    def advance(self, amount=1):
        self.count += amount
        if self.total is not None:
            self.total = max(self.total, self.count)
        self._render()

    def _render(self, force=False, finished=False, failed=False):
        if not self.enabled:
            return
        now = self.clock()
        if not force and now - self.updated < (0.2 if self.terminal else 10.0):
            return
        self.updated = now
        elapsed = max(0, now - self.started)
        label = self.label.ljust(LABEL_WIDTH)
        if self.total is None:
            line = f"{label}: {self.count} files | {_duration(elapsed)} elapsed"
        else:
            percent = 100 if finished else min(99, int(100 * self.count / max(1, self.total)))
            remaining = max(0, self.total - self.count)
            eta = _duration(remaining * elapsed / self.count) if self.count else "--:--"
            if finished:
                eta = "00:00"
            suffix = f" {percent:3d}% {self.count}/{self.total} files | {_duration(elapsed)} elapsed | ETA {eta}"
            width = max(
                4,
                min(
                    20,
                    shutil.get_terminal_size((100, 24)).columns - len(label) - len(suffix) - 3,
                ),
            )
            filled = width * percent // 100
            bar = "#" * filled + "-" * (width - filled)
            line = f"{label} [{bar}]{suffix}"
        if failed:
            line += " | failed"
        elif finished and self.total is None:
            line += " | done"
        if self.terminal:
            # Padding clears a preceding longer line without ANSI control codes.
            self.stream.write("\r" + line.ljust(getattr(self, "line_length", 0)))
            self.line_length = len(line)
            if finished or failed:
                self.stream.write("\n")
                self.line_length = 0
        else:
            self.stream.write(line + "\n")
        self.stream.flush()
