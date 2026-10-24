"""Optional local/LAN HTTP interface. No music, config or database file serving."""

import json
import mimetypes
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .config import load_config
from .jobs import BusyError, JobManager
from .report_store import ReportStore
from .safety import ISSUE_REPORT_FILES, REPORT_FILES, check_outputs, plain_path

ASSETS = {
    "/": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/favicon.svg": "favicon.svg",
}


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, config, db, reports):
        self.jobs = JobManager(config, db, reports)
        self.store = ReportStore(reports)
        super().__init__(address, Handler)

    def server_close(self):
        self.jobs.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "MusicValidator"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        # Request paths can contain arbitrary metadata/search text.
        pass

    def _headers(self, status, content_type, size, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            path = unquote(url.path)
            params = {}
            for key, values in parse_qs(url.query, keep_blank_values=True).items():
                if len(values) != 1:
                    raise ValueError("Query parameters must not repeat")
                params[key] = values[0]
            if path in ASSETS:
                asset = Path(__file__).parent / "web" / ASSETS[path]
                data = asset.read_bytes()
                content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                self._headers(200, content_type + "; charset=utf-8", len(data))
                self.wfile.write(data)
            elif path == "/api/jobs":
                self._json(self.server.jobs.snapshot())
            elif path == "/api/reports":
                with self.server.store.lock:
                    self._json(self.server.store.summaries())
            else:
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "reports"]:
                    with self.server.store.lock:
                        self._json(self.server.store.query(parts[2], parts[3], params))
                elif len(parts) == 4 and parts[0] == "reports":
                    with self.server.store.lock:
                        self._download(*parts[1:])
                else:
                    self._json({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError as exc:
            self._json({"error": str(exc)}, 404)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, 400)

    def _download(self, mode, generation, filename):
        allowed = REPORT_FILES if mode == "configured" else ISSUE_REPORT_FILES
        if filename not in allowed:
            raise ValueError("Unknown report export")
        directory = self.server.store.generation_path(mode, generation)
        with (directory / filename).open("rb") as stream:
            # Downloads keep static HTML standalone and never inject it into the application.
            size = stream.seek(0, 2)
            stream.seek(0)
            self._headers(200, "application/octet-stream", size, filename)
            while chunk := stream.read(65536):
                self.wfile.write(chunk)

    def do_POST(self):
        try:
            # No authentication. Requiring same-origin JSON prevents cross-site form actions.
            if (
                self.headers.get("Content-Type") != "application/json"
                or self.headers.get("X-Music-Validator") != "1"
            ):
                self._json({"error": "Use same-origin JSON requests"}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and (
                urlsplit(origin).scheme not in ("http", "https")
                or urlsplit(origin).netloc != self.headers.get("Host")
            ):
                self._json({"error": "Cross-origin actions are not supported"}, 403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Expected a JSON body of at most 4096 bytes")
            value = json.loads(self.rfile.read(size))
            parts = urlsplit(self.path).path.strip("/").split("/")
            if parts == ["api", "jobs"]:
                self._json(self.server.jobs.start(value), 202)
            elif parts == ["api", "maintenance", "fresh-start"]:
                if value != {"confirmation": "FRESH START"}:
                    raise ValueError("Confirm a fresh start with the exact phrase FRESH START")
                self._json(self.server.jobs.fresh_start(self.server.store))
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                if value != {}:
                    raise ValueError("Cancel takes an empty JSON object")
                self._json(self.server.jobs.cancel(parts[2]), 202)
            else:
                self._json({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BusyError as exc:
            self._json({"error": str(exc)}, 409)
        except KeyError:
            self._json({"error": "Unknown job"}, 404)
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, 400)


def serve(config, db, reports, host="127.0.0.1", port=8765):
    config = config.absolute()
    db, reports = plain_path(db).resolve(), plain_path(reports).resolve()
    settings = load_config(config)
    root = Path(settings["library_root"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Library directory not found: {root}")
    check_outputs(root, db, reports)
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    server = ReportServer((host, port), config, db, reports)

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        print(f"Report server: http://{host}:{server.server_port} (no authentication)", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping report server and its active worker.", file=sys.stderr)
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
    return 0
