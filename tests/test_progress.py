from io import StringIO

import pytest

from music_validator.progress import Progress


class Terminal(StringIO):
    def isatty(self):
        return True


def test_terminal_bar_counts_files_shows_eta_and_finishes_on_newline():
    stream = Terminal()
    clock = [0.0]
    progress = Progress(stream=stream, clock=lambda: clock[0])
    with progress.phase("Analyzing tracks", 2) as advance:
        clock[0] = 1
        advance()
        assert "50% 1/2 files" in stream.getvalue()
        assert "ETA 00:01" in stream.getvalue()
        clock[0] = 2
        advance()
        assert "100%" not in stream.getvalue()
    assert "100% 2/2 files" in stream.getvalue()
    assert "\r" in stream.getvalue() and stream.getvalue().endswith("\n")


def test_logs_are_throttled_and_do_not_contain_terminal_controls():
    stream = StringIO()
    clock = [0.0]
    progress = Progress(stream=stream, clock=lambda: clock[0])
    with progress.phase("Analyzing tracks", 1000) as advance:
        for _ in range(500):
            advance()
        clock[0] = 11
        advance()
        for _ in range(499):
            advance()
    lines = stream.getvalue().splitlines()
    assert len(lines) == 3
    assert "501/1000 files" in lines[1]
    assert "100% 1000/1000 files" in lines[-1]
    assert "\r" not in stream.getvalue() and "\x1b" not in stream.getvalue()


@pytest.mark.parametrize("total, actual", [(0, 0), (3, 2), (1, 2)])
def test_empty_or_changed_estimates_finish_with_actual_counts(total, actual):
    stream = StringIO()
    with Progress(stream=stream).phase("Analyzing tracks", total) as advance:
        advance(actual)
    assert f"100% {actual}/{actual} files" in stream.getvalue()


@pytest.mark.parametrize("error", [OSError("failed read"), KeyboardInterrupt()])
def test_failures_and_interruptions_do_not_claim_completion(error):
    stream = Terminal()
    with (
        pytest.raises(type(error)),
        Progress(stream=stream).phase("Analyzing tracks", 2) as advance,
    ):
        advance()
        raise error
    assert "100%" not in stream.getvalue()
    assert "failed" in stream.getvalue() and stream.getvalue().endswith("\n")


def test_counting_phase_and_disabled_progress():
    stream = StringIO()
    progress = Progress(stream=stream)
    with progress.phase("Counting audio files") as advance:
        advance(25)
    assert "25 files" in stream.getvalue() and "done" in stream.getvalue()
    quiet = StringIO()
    with Progress(stream=quiet, enabled=False).phase("Counting audio files") as advance:
        advance(25)
    assert quiet.getvalue() == ""
