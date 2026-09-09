"""HTTP server for the Medical Summary app — Python standard library only.

The app folder is self-contained: study data is bundled in study_data/, and
the only third-party package is PyMuPDF (optional, for PDF files). Run from
any location with any Python 3.10+:

    python backend/server.py          (from inside the app folder)
or double-click SmartDoc.vbs to launch without a console

The UI is then available at http://localhost:8765.

API
    GET    /api/health                lightweight health and instance identity
    GET    /api/updates               cached update status
    POST   /api/updates/check         check origin for a newer version
    POST   /api/updates/install       download, install, and restart
    GET    /api/status                Ollama + model availability
    POST   /api/jobs                  start a run  {text} | {files: [{filename, content_b64}]}
    GET    /api/jobs/<id>             job progress + result
    GET    /api/history               sidebar list
    GET    /api/history/<id>          full record
    DELETE /api/history/<id>          delete record
    POST   /api/history/<id>/rename   {name}
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The backend modules import each other as plain top-level modules, so the
# folder works wherever it is moved (and whatever it is renamed to).
BACKEND_DIR = Path(__file__).resolve().parent
APP_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import history            # noqa: E402
import pipeline           # noqa: E402
import summarization      # noqa: E402
import transcription      # noqa: E402
import uploads
import exports
from urllib.parse import parse_qs, urlsplit
from updater import Updater, UpdateError, BUSY_PHASES

FRONTEND_DIR = APP_ROOT / "frontend"
# PORT env var (set by dev-preview tooling) overrides the default; when it is
# set we also skip auto-opening the browser.
_PORT_ENV = os.environ.get("PORT")
PORT = int(_PORT_ENV) if _PORT_ENV else 8765
AUTO_OPEN_BROWSER = _PORT_ENV is None

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

INSTANCE_ID = uuid.uuid4().hex


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------ helpers
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        try:
            if self.headers.get("Transfer-Encoding"):
                raise uploads.UploadError("Use Content-Length for requests.")
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                raise ValueError()
            if length > uploads.MAX_REQUEST_BYTES:
                raise uploads.UploadError("Documents exceed the 64 MB combined limit.", 413)
            if self.headers.get_content_type() != "application/json":
                raise uploads.UploadError("Use application/json for this request.", 415)
            self.connection.settimeout(60)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except uploads.UploadError:
            self.close_connection = True
            raise
        except (ValueError, UnicodeDecodeError, TimeoutError):
            self.close_connection = True
            raise uploads.UploadError("Invalid JSON request.") from None

    def _local_request(self):
        port = self.server.server_address[1]
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or (origin and origin != f"http://{host}"):
            self.close_connection = True
            self._send_json({"error": "Only requests from this local app are allowed."}, 403)
            return False
        return True

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):  # quieter console: skip job polls
        if "/api/jobs/" not in self.requestline:
            super().log_request(code, size)

    # ------------------------------------------------ GET
    def do_GET(self):
        if not self._local_request():
            return
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            return self._send_file(FRONTEND_DIR / "index.html")
        if path.startswith("/assets/"):
            name = Path(path[len("/assets/"):]).name  # no traversal
            return self._send_file(FRONTEND_DIR / name)

        if path == "/api/health":
            return self._send_json({"app": "SmartDoc", "instance_id": INSTANCE_ID, "pid": os.getpid()})

        if path == "/api/updates":
            state = self.server.updater.snapshot()
            state["instance_id"] = INSTANCE_ID
            state["active_jobs"] = pipeline.active_job_count()
            return self._send_json(state)

        match = re.fullmatch(r"/api/history/([0-9a-f]+)/export", path)
        if match:
            record = history.get_record(match.group(1))
            if record is None:
                return self._send_json({"error": "Summary not found."}, 404)
            query = parse_qs(urlsplit(self.path).query)
            kind = query.get("kind", ["summary"])[0]
            format = query.get("format", ["txt"])[0]
            preview_pages = None
            try:
                if query.get("preview") == ["1"] and format == "pdf":
                    content, preview_pages = exports.preview_pdf(
                        record, kind, int(query.get("page", ["1"])[0]))
                    filename, mime = "preview.png", "image/png"
                else:
                    filename, mime, content = exports.export_record(record, kind, format)
            except (ValueError, ImportError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            disposition = "inline" if query.get("preview") == ["1"] and kind != "both" else "attachment"
            self.send_header("Content-Disposition", f'{disposition}; filename="{filename}"')
            if preview_pages is not None:
                self.send_header("X-Page-Count", str(preview_pages))
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/status":
            alive = summarization.ollama_alive()
            return self._send_json({
                "ollama": alive,
                "summarizer": {
                    "tag": summarization.SUMMARIZER_MODEL,
                    "available": summarization.summarizer_model_available() if alive else False,
                },
                "vision": {
                    "tag": transcription.VISION_MODEL,
                    "available": transcription.vision_model_available() if alive else False,
                },
                "pdf_support": transcription.pdf_support_available(),
                "ocr": transcription.ocr_engines.status(),
                "export_formats": exports.available_formats(),
            })

        if path == "/api/history":
            return self._send_json(history.list_records())

        m = re.fullmatch(r"/api/history/([0-9a-f]+)", path)
        if m:
            record = history.get_record(m.group(1))
            if record is None:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(record)

        m = re.fullmatch(r"/api/jobs/([0-9a-f]+)", path)
        if m:
            job = pipeline.get_job(m.group(1))
            if job is None:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(job)

        return self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------ POST
    def do_POST(self):
        if not self._local_request():
            return
        path = self.path.split("?")[0]
        try:
            body = self._read_json_body()
        except uploads.UploadError as exc:
            return self._send_json({"error": str(exc)}, exc.status)

        if path == "/api/updates/check":
            self.server.updater.request_check()
            return self._send_json({"ok": True}, 202)
        if path == "/api/updates/install":
            try:
                self.server.updater.request_install()
                return self._send_json({"ok": True, "instance_id": INSTANCE_ID}, 202)
            except (UpdateError, RuntimeError) as exc:
                return self._send_json({"error": str(exc)}, 409)

        if self.server.updater.snapshot()["phase"] in BUSY_PHASES:
            return self._send_json({"error": "An update is being installed. Please wait for the app to restart."}, 409)

        if path == "/api/jobs":
            try:
                payload = uploads.parse_payload(body)
                job_id = pipeline.start_job(payload)
                return self._send_json({"job_id": job_id}, 202)
            except uploads.UploadError as exc:
                return self._send_json({"error": str(exc)}, exc.status)
            except RuntimeError as exc:
                return self._send_json({"error": str(exc)}, 409)

        m = re.fullmatch(r"/api/history/([0-9a-f]+)/rename", path)
        if m:
            name = (body.get("name") or "").strip()
            if not name:
                return self._send_json({"error": "name required"}, 400)
            if history.rename_record(m.group(1), name):
                return self._send_json({"ok": True})
            return self._send_json({"error": "not found"}, 404)

        return self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------ DELETE
    def do_DELETE(self):
        if not self._local_request():
            return
        if self.server.updater.snapshot()["phase"] in BUSY_PHASES:
            return self._send_json({"error": "Please wait for the app to restart."}, 409)
        m = re.fullmatch(r"/api/history/([0-9a-f]+)", self.path.split("?")[0])
        if m:
            if history.delete_record(m.group(1)):
                return self._send_json({"ok": True})
            return self._send_json({"error": "not found"}, 404)
        return self._send_json({"error": "not found"}, 404)


def main() -> None:
    from runtime import open_log, spawn_server
    # pythonw has no stdout/stderr. Keep startup and server errors available.
    log = open_log()
    sys.stdout = sys.stderr = log
    (APP_ROOT / "data" / "history").mkdir(parents=True, exist_ok=True)
    (APP_ROOT / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    restart_requested = threading.Event()

    def restart():
        restart_requested.set()
        # Return the install response before the listening socket is closed.
        threading.Timer(1.0, server.shutdown).start()

    server.updater = Updater(APP_ROOT.parent, pipeline.pause_for_update,
                             pipeline.resume_after_update, restart)
    server.updater.start()
    url = f"http://localhost:{PORT}"
    print(f"SmartDoc running at {url}", flush=True)
    if AUTO_OPEN_BROWSER:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.updater.stop()
        server.server_close()
    if restart_requested.is_set():
        spawn_server(PORT)


if __name__ == "__main__":
    main()
