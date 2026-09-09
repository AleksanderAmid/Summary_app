"""Focused tests; all documents and Git remotes here are synthetic/local.

Run: python -B -m unittest discover -s app/tests -p test_improvements.py -v
"""
import base64
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import pipeline
import privacy
import server
import uploads
from updater import Updater

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def encoded(name="note.txt", content=b"Synthetic test document"):
    return {"filename": name, "content_b64": base64.b64encode(content).decode()}


class UploadTests(unittest.TestCase):
    def test_ten_documents_and_legacy_file(self):
        self.assertEqual(len(uploads.parse_payload({"files": [encoded()] * 10})["files"]), 10)
        self.assertEqual(uploads.parse_payload(encoded())["files"][0]["content"], b"Synthetic test document")
        self.assertEqual(uploads.parse_payload({"text": "hello"}), {"text": "hello"})

    def test_reject_bad_count_content_type_and_mixed_input(self):
        cases = [{"files": []}, {"files": [encoded()] * 11}, {"files": "bad"},
                 {"files": [None]}, encoded("bad.exe"), encoded(content=b""),
                 {"filename": "note.txt", "content_b64": "!"},
                 {"files": [encoded()], "text": "other"}, [], {"text": 42}]
        for body in cases:
            with self.subTest(body=repr(body)[:80]), self.assertRaises(uploads.UploadError):
                uploads.parse_payload(body)

    def test_total_size_limit_and_safe_names(self):
        with patch.object(uploads, "MAX_UPLOAD_BYTES", 6):
            with self.assertRaises(uploads.UploadError) as caught:
                uploads.parse_payload({"files": [encoded(content=b"1234")] * 2})
            self.assertEqual(caught.exception.status, 413)
        payload = uploads.parse_payload(encoded(r"..\..\folder\note.txt"))
        self.assertEqual(payload["files"][0]["filename"], "note.txt")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(pipeline, "UPLOAD_DIR", Path(self.folder.name)))
        self.stack.enter_context(patch.object(pipeline, "_jobs", {}))
        self.stack.enter_context(patch.object(pipeline, "_updates_paused", False))
        self.stack.enter_context(patch.object(privacy, "PRIVATE_DIR", Path(self.folder.name) / "private"))
        self.stack.enter_context(patch.object(pipeline.anonymization, "run_anonymization_stage",
                                             side_effect=lambda text, *args, **kwargs: {"text": text, "phi_hits": {}, "mapping": {}, "implemented": True}))
        self.generated = self.stack.enter_context(patch.object(pipeline.summarization, "summarize",
                      return_value={"summary": "Combined test summary",
                                    "telemetry": {"eval_count": 3, "wall_seconds": 0}}))
        self.saved = self.stack.enter_context(patch.object(pipeline.history, "save_record"))

    def run_payload(self, payload):
        pipeline._jobs["test"] = {"status": "running", "stages": pipeline._new_stages()}
        return pipeline._execute("test", payload)

    def test_combines_ten_in_order_and_preserves_duplicate_names(self):
        documents = [{"filename": "same.txt", "content": ("Test document " + str(i)).encode()}
                     for i in range(10)]
        record = self.run_payload({"files": documents})
        source = self.generated.call_args.args[0]
        self.assertIn("--- Document 1 ---\nTest document 0\n\n", source)
        self.assertTrue(source.endswith("Test document 9"))
        self.assertEqual(record["input"]["document_count"], 10)
        self.assertEqual(len(list(Path(self.folder.name).glob("*.txt"))), 10)
        self.generated.assert_called_once()
        self.saved.assert_called_once()
        self.assertEqual(record["input"]["words"], len(source.split()))

    def test_pasted_text_still_skips_transcription(self):
        record = self.run_payload({"text": "Plain synthetic text"})
        self.assertEqual(record["source_text"], "Plain synthetic text")
        self.assertEqual(record["stages"][0]["status"], "skipped")

    def test_unreadable_document_prevents_partial_summary(self):
        with patch.object(pipeline.transcription, "transcribe_file",
                          return_value={"full_text": "", "pages": [], "method": "test"}):
            with self.assertRaisesRegex(RuntimeError, "no text"):
                self.run_payload({"files": [{"filename": "empty.txt", "content": b"  "}]})
        self.generated.assert_not_called()
        self.saved.assert_not_called()

    def test_update_gate_preserves_running_jobs(self):
        pipeline._jobs["running"] = {"status": "running"}
        with self.assertRaisesRegex(RuntimeError, "still running"):
            pipeline.pause_for_update()
        pipeline._jobs.clear()
        pipeline.pause_for_update()
        with self.assertRaisesRegex(RuntimeError, "update"):
            pipeline.start_job({"text": "test"})
        pipeline.resume_after_update()
        self.assertFalse(pipeline._updates_paused)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.updater = Mock()
        self.httpd.updater.snapshot.return_value = {"phase": "current"}
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, body, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("POST", "/api/jobs", body=body,
                           headers=headers or {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_batch_reaches_pipeline_and_invalid_json_is_rejected(self):
        with patch.object(pipeline, "start_job", return_value="aabb") as start:
            status, result = self.request(json.dumps({"files": [encoded()] * 10}))
            self.assertEqual((status, result["job_id"]), (202, "aabb"))
            self.assertEqual(len(start.call_args.args[0]["files"]), 10)
        self.assertEqual(self.request("{")[0], 400)
        self.assertEqual(self.request("[]")[0], 400)
        self.assertEqual(self.request(json.dumps({"files": [encoded()] * 11}))[0], 400)

    def test_update_blocks_jobs_and_cross_origin_mutations(self):
        self.httpd.updater.snapshot.return_value = {"phase": "installing"}
        with patch.object(pipeline, "start_job") as start:
            self.assertEqual(self.request(json.dumps({"text": "test"}))[0], 409)
            start.assert_not_called()
        self.assertEqual(self.request("{}", {"Content-Type": "application/json",
                                            "Origin": "https://unrelated.example"})[0], 403)

    def test_exports_are_uncached_and_pseudonymised_download_keeps_tokens(self):
        record = {"summary": "Erik Example. [E0001]", "pseudonymised_text": "[NAME_01]",
                  "pseudonymisation": {"implemented": True}, "evidence": []}
        with patch.object(server.history, "get_record", return_value=record):
            for query, mime in (("kind=pseudonymised&format=txt", "text/plain"),
                                ("kind=both&format=docx", "application/zip"),
                                ("kind=pseudonymised&format=pdf&preview=1", "image/png")):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                self.addCleanup(connection.close)
                connection.request("GET", "/api/history/aabb/export?" + query)
                response = connection.getresponse()
                content = response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader("Content-Type").startswith(mime))
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                if mime == "text/plain":
                    self.assertIn(b"[NAME_01]", content)
                    self.assertNotIn(b"Erik", content)
                if mime == "image/png":
                    self.assertEqual(response.getheader("X-Page-Count"), "1")
                    self.assertTrue(content.startswith(bytes([137,80,78,71])))

    def test_update_endpoint_conflict_is_visible(self):
        self.httpd.updater.request_install.side_effect = RuntimeError("A summary is still running.")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("POST", "/api/updates/install", "{}", {"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 409)
        self.assertIn("still running", json.loads(response.read())["error"])


@unittest.skipUnless(shutil.which("git"), "Git required")
class UpdateTests(unittest.TestCase):
    def git(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            self.fail(result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.seed, self.remote, self.clone = [self.base / name for name in ("seed", "remote.git", "installed")]
        self.seed.mkdir()
        self.git(self.seed, "init", "-b", "main")
        self.git(self.seed, "config", "user.email", "test@example.invalid")
        self.git(self.seed, "config", "user.name", "SmartDoc Test")
        (self.seed / "app/backend").mkdir(parents=True)
        (self.seed / "app/backend/server.py").write_text("VERSION = 1\n", encoding="utf-8")
        (self.seed / ".gitignore").write_text("app/data/\n__pycache__/\n", encoding="utf-8")
        self.commit("Initial")
        self.git(self.base, "clone", "--bare", str(self.seed), str(self.remote))
        self.git(self.base, "clone", str(self.remote), str(self.clone))
        self.git(self.seed, "remote", "add", "origin", str(self.remote))
        self.old = self.git(self.clone, "rev-parse", "HEAD")
        self.pause, self.resume, self.restart = Mock(), Mock(), Mock()
        self.updater = Updater(self.clone, self.pause, self.resume, self.restart)

    def commit(self, message):
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", message)

    def publish(self, source="VERSION = 2\n"):
        (self.seed / "app/backend/server.py").write_text(source, encoding="utf-8")
        self.commit("New version")
        self.git(self.seed, "push", "origin", "main")

    def install(self):
        self.updater.request_install()
        deadline = time.monotonic() + 20
        while self.updater.snapshot()["phase"] in {"downloading", "installing"}:
            if time.monotonic() > deadline:
                self.fail("Installer timed out")
            time.sleep(.05)
        return self.updater.snapshot()

    def test_check_is_read_only_and_install_preserves_data(self):
        self.assertEqual(self.updater.check()["phase"], "current")
        self.publish()
        state = self.updater.check()
        self.assertTrue(state["available"])
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)
        data = self.clone / "app/data/history/user.json"
        data.parent.mkdir(parents=True)
        data.write_text('{"test":true}', encoding="utf-8")
        self.assertEqual(self.install()["phase"], "restarting")
        self.assertNotEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)
        self.assertEqual(data.read_text(), '{"test":true}')
        self.restart.assert_called_once()
        self.resume.assert_not_called()

    def test_local_edits_are_not_overwritten(self):
        self.publish()
        local = self.clone / "app/backend/server.py"
        local.write_text("LOCAL = True\n", encoding="utf-8")
        self.assertFalse(self.updater.check()["can_install"])
        self.assertEqual(self.install()["phase"], "error")
        self.assertEqual(local.read_text(), "LOCAL = True\n")
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)
        self.restart.assert_not_called()
        self.resume.assert_called_once()

    def test_failed_dependency_install_restores_previous_code(self):
        (self.seed / "app/requirements.txt").write_text("synthetic-package==1\n", encoding="utf-8")
        self.publish()
        actual_run = subprocess.run
        def fail_pip(command, *args, **kwargs):
            if command[1:4] == ["-m", "pip", "install"]:
                return subprocess.CompletedProcess(command, 1, b"", b"Synthetic failure")
            return actual_run(command, *args, **kwargs)
        with patch("updater.subprocess.run", side_effect=fail_pip):
            state = self.install()
        self.assertEqual(state["phase"], "error")
        self.assertIn("dependencies", state["message"])
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)
        self.restart.assert_not_called()
        self.resume.assert_called_once()

    def test_invalid_release_rolls_back_without_restart(self):
        self.publish("this is invalid python !!!\n")
        self.assertEqual(self.install()["phase"], "error")
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)
        self.assertEqual((self.clone / "app/backend/server.py").read_text(), "VERSION = 1\n")
        self.restart.assert_not_called()

    def test_diverged_checkout_and_active_jobs_are_blocked(self):
        self.publish()
        self.git(self.clone, "config", "user.email", "test@example.invalid")
        self.git(self.clone, "config", "user.name", "SmartDoc Test")
        (self.clone / "local.txt").write_text("Local change", encoding="utf-8")
        self.git(self.clone, "add", ".")
        self.git(self.clone, "commit", "-m", "Local commit")
        local_head = self.git(self.clone, "rev-parse", "HEAD")
        self.assertIn("diverged", self.install()["message"])
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), local_head)
        self.pause.side_effect = RuntimeError("A summary is still running.")
        with self.assertRaisesRegex(RuntimeError, "still running"):
            self.updater.request_install()
        self.restart.assert_not_called()

    def test_network_failure_and_missing_git_do_not_change_checkout(self):
        self.updater.git = None
        self.assertEqual(self.updater.check()["phase"], "error")
        self.updater.git = shutil.which("git")
        self.git(self.clone, "remote", "set-url", "origin", str(self.base / "nonexistent"))
        self.assertEqual(self.updater.check()["phase"], "error")
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)

    def test_update_cannot_replace_stored_documents(self):
        self.publish()
        path = self.seed / "app/data/uploads/test.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Remote data", encoding="utf-8")
        self.git(self.seed, "add", "-f", "app/data/uploads/test.txt")
        self.git(self.seed, "commit", "-m", "Change data")
        self.git(self.seed, "push", "origin", "main")
        self.assertIn("stored app data", self.install()["message"])
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.old)

    def test_real_server_download_and_restart(self):
        # Upgrade this synthetic remote to the actual application code.
        for source in (SOURCE_ROOT / "app/backend").glob("*.py"):
            shutil.copy2(source, self.seed / "app/backend" / source.name)
        shutil.copytree(SOURCE_ROOT / "app/frontend", self.seed / "app/frontend")
        self.commit("Actual app")
        self.git(self.seed, "push", "origin", "main")
        self.git(self.clone, "pull", "--ff-only")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, PORT=str(port), PYTHONDONTWRITEBYTECODE="1")
        child = subprocess.Popen([sys.executable, "-B", "app/backend/server.py"],
                                 cwd=self.clone, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pids = {child.pid}
        def cleanup():
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.addCleanup(cleanup)
        def api(path, post=False):
            request = urllib.request.Request("http://127.0.0.1:" + str(port) + path,
                       data=b"{}" if post else None,
                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.load(response)
        def wait_for(predicate, timeout=25):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    result = predicate()
                    if result:
                        return result
                except (OSError, ValueError):
                    pass
                time.sleep(.1)
            self.fail("Server did not reach expected state")
        original = wait_for(lambda: api("/api/health"))
        wait_for(lambda: api("/api/updates")["phase"] == "current")
        (self.seed / "release.txt").write_text("Synthetic update", encoding="utf-8")
        self.commit("Download test")
        self.git(self.seed, "push", "origin", "main")
        api("/api/updates/check", True)
        wait_for(lambda: api("/api/updates")["phase"] == "available")
        api("/api/updates/install", True)
        updated = wait_for(lambda: (health if health["instance_id"] != original["instance_id"] else None)
                           if (health := api("/api/health")) else None)
        pids.add(updated["pid"])
        self.assertNotEqual(updated["instance_id"], original["instance_id"])
        self.assertEqual((self.clone / "release.txt").read_text(), "Synthetic update")


if __name__ == "__main__":
    unittest.main()
