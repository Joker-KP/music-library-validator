"""Isolated scan worker. Stdout is a private JSON-lines event channel."""

import argparse
import json
import signal
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from .cli import run


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "db", "reports"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--options", required=True)
    args = parser.parse_args()
    options = json.loads(args.options)
    output = sys.stdout

    def emit(event):
        print(json.dumps(event, ensure_ascii=False), file=output, flush=True)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with redirect_stdout(sys.stderr):
            code = run(
                args.config,
                args.db,
                args.reports,
                options["full_scan"],
                show_progress=False,
                event_callback=emit,
                audio_integrity_only=options["mode"] == "integrity",
                audio_identity_only=options["mode"] == "identity",
                recheck_integrity=options["recheck_integrity"],
                recheck_identity=options["recheck_identity"],
            )
        return code
    except KeyboardInterrupt:
        emit({"type": "cancelled"})
        return 130
    except (OSError, ValueError, yaml.YAMLError, sqlite3.Error) as exc:
        emit({"type": "error", "message": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
