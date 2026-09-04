from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from catalog import list_sessions, public_record, session_records, usage

log = logging.getLogger("claude-sniff")

DIST = Path(__file__).resolve().parent / "dist"

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


_SPA = {"/", "/index.html", "/usage", "/proxy", "/info"}


def _dist_file(url_path: str) -> Path | None:
    path = url_path.rstrip("/") or "/"
    rel = "index.html" if path in _SPA else url_path.lstrip("/")
    if not rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = DIST.resolve()
    if not root.is_dir():
        return None
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


STUB_PROXY = {
    "proxy": False,
    "intercept": False,
    "error": None,
    "head": None,
    "queue": [],
    "listen": None,
    "ca": None,
    "sniffed": False,
    "live": [],
}


def make_handler(
    logs_dir: Path,
    projects_dir: Path | None,
    control: Any | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/proxy":
                self._json(control.snapshot() if control is not None else STUB_PROXY)
                return
            if path == "/api/usage":
                qs = parse_qs(parsed.query)
                period = (qs.get("period") or [None])[0]
                source = (qs.get("source") or [None])[0] or None
                model = (qs.get("model") or [None])[0] or None
                project = (qs.get("project") or [None])[0] or None
                try:
                    self._json(
                        usage(
                            logs_dir,
                            projects_dir,
                            period=period,
                            source=source,
                            model=model,
                            project=project,
                        )
                    )
                except ValueError:
                    self.send_error(400, "bad usage query")
                return
            if path == "/api/sessions":
                self._json({"sessions": list_sessions(logs_dir, projects_dir)})
                return
            prefix = "/api/sessions/"
            if path.startswith(prefix):
                parts = path[len(prefix) :].split("/")
                if len(parts) == 2:
                    records = session_records(logs_dir, projects_dir, parts[0], parts[1])
                    if records is None:
                        self.send_error(404, "session not found")
                        return
                    self._json({"client": parts[0], "id": parts[1], "records": records})
                    return
            static = _dist_file(path)
            if static is not None:
                self._file(static, _TYPES.get(static.suffix.lower(), "application/octet-stream"))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if control is None:
                self.send_error(404)
                return
            body = self._read_json()
            if path == "/api/proxy":
                if "proxy" in body:
                    control.set_proxy(bool(body["proxy"]))
                if "intercept" in body:
                    control.set_intercept(bool(body["intercept"]))
                self._json(control.snapshot())
                return
            if path == "/api/intercept/forward":
                self._json(control.forward(str(body.get("body") or "")))
                return
            if path == "/api/intercept/drop":
                self._json(control.drop())
                return
            self.send_error(404)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(data)

        def _json(self, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(data)

        def _safe_write(self, data: bytes) -> None:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def log_message(self, fmt: str, *args: Any) -> None:
            if "/api/" in self.path:
                return
            log.debug(fmt, *args)

        def log_error(self, fmt: str, *args: Any) -> None:
            log.debug(fmt, *args)

    return Handler


class _UIServer(ThreadingHTTPServer):
    allow_reuse_address = True


def start_ui(
    logs_dir: Path,
    port: int,
    projects_dir: Path | None = None,
    control: Any | None = None,
) -> ThreadingHTTPServer:
    if not DIST.is_dir():
        log.warning("UI build missing at %s — run: cd ui && bun install && bun run build", DIST)
    httpd = _UIServer(("127.0.0.1", port), make_handler(logs_dir, projects_dir, control))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve_blocking(
    logs_dir: Path,
    port: int,
    projects_dir: Path | None = None,
    control: Any | None = None,
) -> ThreadingHTTPServer:
    httpd = start_ui(logs_dir, port, projects_dir, control)
    log.info("UI: http://127.0.0.1:%d", httpd.server_address[1])
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        log.info("Stop")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return httpd
