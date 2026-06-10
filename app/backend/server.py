"""HTTP server for the Medical Summary app — Python stdlib only.

Run with the project virtual environment (needs requests / pymupdf /
python-docx, all already installed):

    .venv\\Scripts\\python.exe -m app.backend.server        (from repo root)
or simply double-click app\\run_app.bat

The UI is then available at http://localhost:8765.

API
    GET    /api/status                Ollama + model availability
    POST   /api/jobs                  start a run  {text} | {filename, content_b64}
    GET    /api/jobs/<id>             job progress + result
    GET    /api/history               sidebar list
    GET    /api/history/<id>          full record
    DELETE /api/history/<id>          delete record
    POST   /api/history/<id>/rename   {name}
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Allow running both as a module (-m app.backend.server) and as a script.
APP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_ROOT.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))
    from app.backend import history, pipeline, summarization, transcription
else:
    from . import history, pipeline, summarization, transcription

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

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MB decoded


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
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        try:
            return json.loads(text)
        except Exception:
            return {}

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):  # quieter console: skip job polls
        if "/api/jobs/" not in self.requestline:
            super().log_request(code, size)

    # ------------------------------------------------ GET
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            return self._send_file(FRONTEND_DIR / "index.html")
        if path.startswith("/assets/"):
            name = Path(path[len("/assets/"):]).name  # no traversal
            return self._send_file(FRONTEND_DIR / name)

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
        path = self.path.split("?")[0]
        # Read the body unconditionally: leaving it unread on an error path
        # desynchronizes the HTTP/1.1 keep-alive connection (the next request
        # on the socket would be parsed out of the leftover body bytes).
        body = self._read_json_body()

        if path == "/api/jobs":
            payload: dict = {}
            # A present filename routes to the file branch even when the
            # payload is empty, so a 0-byte upload gets a clear error
            # instead of falling through to the text branch.
            if body.get("filename") or body.get("content_b64"):
                try:
                    content = base64.b64decode(body.get("content_b64") or "")
                except Exception:
                    return self._send_json({"error": "invalid base64 payload"}, 400)
                if not content:
                    return self._send_json({"error": "uploaded file is empty"}, 400)
                if len(content) > MAX_UPLOAD_BYTES:
                    return self._send_json({"error": "file too large (max 64 MB)"}, 413)
                filename = (body.get("filename") or "upload").strip()
                ext = Path(filename).suffix.lower()
                if ext not in transcription.SUPPORTED_EXTENSIONS:
                    return self._send_json(
                        {"error": f"unsupported file type '{ext}' — supported: "
                                  + ", ".join(transcription.SUPPORTED_EXTENSIONS)}, 400)
                payload = {"filename": filename, "content": content}
            elif (body.get("text") or "").strip():
                payload = {"text": body["text"]}
            else:
                return self._send_json({"error": "provide 'text' or "
                                                 "'filename'+'content_b64'"}, 400)
            job_id = pipeline.start_job(payload)
            return self._send_json({"job_id": job_id}, 202)

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
        m = re.fullmatch(r"/api/history/([0-9a-f]+)", self.path.split("?")[0])
        if m:
            if history.delete_record(m.group(1)):
                return self._send_json({"ok": True})
            return self._send_json({"error": "not found"}, 404)
        return self._send_json({"error": "not found"}, 404)


def main() -> None:
    (APP_ROOT / "data" / "history").mkdir(parents=True, exist_ok=True)
    (APP_ROOT / "data" / "uploads").mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Medical Summary app running at {url}")
    print(f"  summarizer: {summarization.SUMMARIZER_MODEL}")
    print(f"  vision:     {transcription.VISION_MODEL}")
    print("Press Ctrl+C to stop.")
    if AUTO_OPEN_BROWSER:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
