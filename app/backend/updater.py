"""Opt-in Git updates. Checks never change the checkout; installation is ff-only."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

CHECK_INTERVAL = 30 * 60
BUSY_PHASES = {"downloading", "installing", "restarting"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(RuntimeError):
    pass


class Updater:
    def __init__(self, repo_root, pause_jobs, resume_jobs, restart):
        self.root = Path(repo_root).resolve()
        self.pause_jobs, self.resume_jobs, self.restart = pause_jobs, resume_jobs, restart
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._stop = threading.Event()
        self.state = {"phase": "idle", "available": False, "current": None,
                      "latest": None, "checked_at": None, "message": "",
                      "can_install": False}
        self.git = shutil.which("git")

    def _set(self, **values):
        with self._lock:
            self.state.update(values)

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def _git(self, *args, timeout=30, check=True):
        if not self.git:
            raise UpdateError("Install Git for Windows to enable app updates.")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        try:
            result = subprocess.run(
                [self.git, "-c", "core.hooksPath=" + os.devnull, *args],
                cwd=self.root, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise UpdateError("The update server took too long to respond. Please retry.") from None
        if check and result.returncode:
            # Do not expose remote URLs/credentials or arbitrary Git output in the UI.
            raise UpdateError("Git could not complete the update. Check your connection "
                              "and repository access, then retry.")
        return result

    def _branch(self):
        if not (self.root / ".git").exists():
            raise UpdateError("Updates require the full Git clone, including its .git folder.")
        branch = self._git("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        remote = self._git("config", "--get", f"branch.{branch}.remote").stdout.strip()
        ref = self._git("config", "--get", f"branch.{branch}.merge").stdout.strip()
        if remote != "origin" or not ref.startswith("refs/heads/"):
            raise UpdateError("This branch must track a branch on origin to receive updates.")
        self._git("check-ref-format", ref)
        return ref

    def _dirty(self):
        return bool(self._git("status", "--porcelain", "--untracked-files=normal", "--", ".", ":(exclude)app/data").stdout.strip())

    def _inspect_remote(self):
        ref = self._branch()
        current = self._git("rev-parse", "HEAD").stdout.strip()
        output = self._git("ls-remote", "--exit-code", "origin", ref, timeout=45).stdout
        matches = [line.split()[0] for line in output.splitlines()
                   if len(line.split()) == 2 and line.split()[1] == ref]
        if len(matches) != 1:
            raise UpdateError("The tracked branch is no longer available on origin.")
        latest = matches[0]
        available = latest != current
        if available and self._git("cat-file", "-e", latest + "^{commit}", check=False).returncode == 0:
            # A development checkout can be ahead of origin; that is not an update.
            available = self._git("merge-base", "--is-ancestor", latest, current,
                                  check=False).returncode != 0
        dirty = self._dirty()
        message = ("An update is ready to download. The app will restart after installation."
                   if available else "You're up to date.")
        if available and dirty:
            message = "An update is available. Save or commit local changes before installing."
        self._set(current=current, latest=latest, available=available,
                  checked_at=time.time(), can_install=available and not dirty,
                  message=message)
        return ref, current, latest, available

    def check(self):
        if not self._operation.acquire(blocking=False):
            return self.snapshot()
        try:
            if self.snapshot()["phase"] in BUSY_PHASES:
                return self.snapshot()
            self._set(phase="checking", can_install=False, message="Checking for a newer version...")
            self._inspect_remote()
            self._set(phase="available" if self.snapshot()["available"] else "current")
        except (UpdateError, OSError) as exc:
            self._set(phase="error", can_install=False, message=str(exc))
        finally:
            self._operation.release()
        return self.snapshot()

    def request_check(self):
        threading.Thread(target=self.check, daemon=True).start()

    def start(self):
        def loop():
            while not self._stop.is_set():
                self.check()
                self._stop.wait(CHECK_INTERVAL)
        threading.Thread(target=loop, daemon=True, name="update-checker").start()

    def stop(self):
        self._stop.set()

    def request_install(self):
        if not self._operation.acquire(blocking=False):
            raise UpdateError("An update check or installation is already in progress.")
        try:
            self.pause_jobs()
            self._set(phase="downloading", can_install=False,
                      message="Downloading the latest update…")
            threading.Thread(target=self._install, daemon=True, name="update-installer").start()
        except Exception:
            self._operation.release()
            raise

    def _install(self):
        previous = None
        applied = False
        restarting = False
        try:
            ref, previous, latest, available = self._inspect_remote()
            if not available:
                self._set(phase="current", can_install=False)
                return
            if self._dirty():
                raise UpdateError("Local files have changed. Save or commit them before "
                                  "installing; your changes have been kept.")
            self._set(phase="downloading", can_install=False,
                      message="Downloading the latest update…")
            self._git("fetch", "--no-tags", "origin", ref, timeout=180)
            target = self._git("rev-parse", "FETCH_HEAD").stdout.strip()
            if target != latest:
                raise UpdateError("A newer update appeared during download. Please check again.")
            if self._git("merge-base", "--is-ancestor", previous, target,
                         check=False).returncode:
                raise UpdateError("Local and remote versions have diverged. A manual merge "
                                  "is needed; your files have been kept.")
            # Recheck after the network call, before modifying any files.
            if self._dirty() or self._git("rev-parse", "HEAD").stdout.strip() != previous:
                raise UpdateError("Local files changed during download. Please retry after saving them.")
            # Runtime data is never replaced, including legacy data tracked by the repository.
            data_changes = self._git("diff", "--name-only", previous, target, "--", "app/data").stdout.strip()
            if data_changes:
                raise UpdateError("This update changes stored app data and requires a manual update. "
                                  "Your documents and history have been kept.")
            self._set(phase="installing", message="Installing and checking the update…")
            self._git("merge", "--ff-only", "--no-edit", target, timeout=60)
            applied = True
            # Import the updated server in a separate process before stopping the current one.
            # Missing/new dependencies or invalid Python leave the current app available.
            probe = subprocess.run(
                [sys.executable, "-B", "-c",
                 "import runpy; runpy.run_path('app/backend/server.py', run_name='update_preflight')"],
                cwd=self.root, capture_output=True, timeout=45, creationflags=NO_WINDOW,
            )
            if probe.returncode:
                raise UpdateError("The new version could not start with this Python setup. "
                                  "The previous version has been restored. Check its setup requirements.")
            self._set(phase="restarting", latest=target, can_install=False,
                      message="Update installed. Restarting SmartDoc…")
            self.restart()
            restarting = True
        except Exception as exc:
            message = str(exc) if isinstance(exc, (UpdateError, RuntimeError)) else "The update failed. Please retry."
            if applied:
                # --keep refuses to overwrite edits made concurrently by a developer.
                try:
                    self._git("reset", "--keep", previous)
                except Exception:
                    message = "Update recovery needs attention. Local edits were preserved; inspect the Git checkout before restarting."
            self._set(phase="error", can_install=False, message=message)
        finally:
            if not restarting:
                self.resume_jobs()
            self._operation.release()
