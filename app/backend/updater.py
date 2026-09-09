"""Opt-in Git updates that replace app code while preserving local runtime data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

CHECK_INTERVAL = 30 * 60
BUSY_PHASES = {"downloading", "installing", "restarting"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CODE_PATHS = (".", ":(exclude)app/data")


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

    def _git(self, *args, timeout=30, check=True, index_file=None):
        if not self.git:
            raise UpdateError("Install Git for Windows to enable app updates.")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        if index_file is not None:
            env["GIT_INDEX_FILE"] = str(index_file)
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

    def _backup_edits(self, previous, backup):
        # Keep a local recovery reference even when an installation has its own commits.
        self._git("update-ref", "refs/smartdoc-backups/" + backup.name, previous)
        edits = None
        if self._git("status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS).stdout.strip():
            # A normal path-limited stash still includes ALL staged files. Build
            # the stash with a separate index so newly staged patient data never
            # enters either backup tree. The real index/worktree are untouched.
            index = self.root / self._git("rev-parse", "--git-path", "index").stdout.strip()
            temporary_index = backup / "index"
            shutil.copy2(index, temporary_index)
            try:
                if (self._git("ls-files", "--", "app/data").stdout.strip()
                        or self._git("ls-tree", previous, "--", "app/data").stdout.strip()):
                    self._git("restore", "--source=" + previous, "--staged", "--", "app/data", index_file=temporary_index)
                staged_tree = self._git("write-tree", index_file=temporary_index).stdout.strip()
                identity = ("-c", "user.name=SmartDoc updater", "-c", "user.email=updater@smartdoc.invalid")
                staged_commit = self._git(*identity, "commit-tree", staged_tree, "-p", previous,
                                          "-m", "SmartDoc staged code backup").stdout.strip()
                self._git("add", "-u", "--", *CODE_PATHS, index_file=temporary_index)
                worktree = self._git("write-tree", index_file=temporary_index).stdout.strip()
                edits = self._git(*identity, "commit-tree", worktree, "-p", previous, "-p", staged_commit,
                                 "-m", "SmartDoc before update " + backup.name).stdout.strip()
                self._git("update-ref", "--create-reflog", "-m", "SmartDoc before update " + backup.name,
                          "refs/stash", edits)
            finally:
                temporary_index.unlink(missing_ok=True)
        return edits

    def _backup_obstructions(self, target, backup, moved):
        """Move only untracked/ignored paths that the incoming code would replace."""
        tracked = set(self._git("ls-files", "-z").stdout.split("\0"))
        incoming = self._git("ls-tree", "-r", "--name-only", "-z", target).stdout.split("\0")
        collisions = set()
        for name in incoming:
            if not name or name == "app/data" or name.startswith("app/data/"):
                continue
            path = self.root / name
            # A file or symlink can also obstruct a new directory further down the path.
            for parent in reversed(path.relative_to(self.root).parents):
                candidate = self.root / parent
                if candidate.is_symlink() or (candidate.exists() and not candidate.is_dir()):
                    if parent.as_posix() not in tracked:
                        collisions.add(candidate)
                    break
            else:
                if name not in tracked and (path.exists() or path.is_symlink()):
                    collisions.add(path)
        for path in sorted(collisions, key=lambda p: len(p.parts)):
            if any(path.is_relative_to(saved) for saved, _ in moved):
                continue
            destination = backup / "files" / path.relative_to(self.root)
            if not path.parent.resolve().is_relative_to(self.root) or not destination.resolve().is_relative_to(backup):
                raise UpdateError("A local file points outside the app folder. It has been kept.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.rename(destination)
            moved.append((path, destination))

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
        message = ("An update is ready to download. The app will restart after installation."
                   if available else "You're up to date.")
        self._set(current=current, latest=latest, available=available,
                  checked_at=time.time(), can_install=available,
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
        edits = None
        edits_cleared = False
        moved = []
        applied = False
        restarting = False
        try:
            ref, previous, _, available = self._inspect_remote()
            if not available:
                self._set(phase="current", can_install=False)
                return
            self._set(phase="downloading", can_install=False,
                      message="Downloading the latest update…")
            self._git("fetch", "--no-tags", "origin", ref, timeout=180)
            target = self._git("rev-parse", "FETCH_HEAD").stdout.strip()
            # Use the fetched tip even if a release arrived during the check/download.
            self._set(latest=target)
            if self._git("rev-parse", "HEAD").stdout.strip() != previous or self._branch() != ref:
                raise UpdateError("The app version changed during download. Please retry.")
            # Runtime data is never replaced, including legacy data tracked by the repository.
            data_changes = self._git("diff", "--name-only", previous, target, "--", "app/data").stdout.strip()
            if data_changes:
                raise UpdateError("This update changes stored app data and requires a manual update. "
                                  "Your documents and history have been kept.")
            self._set(phase="installing", message="Installing and checking the update…")
            backup = self.root / "app/data/update-backups" / uuid.uuid4().hex
            backup.mkdir(parents=True)
            edits = self._backup_edits(previous, backup)
            (backup / "recovery.json").write_text(json.dumps({"previous": previous, "edits": edits}), encoding="utf-8")
            if edits:
                edits_cleared = True
                self._git("restore", "--source=" + previous, "--staged", "--worktree", "--", *CODE_PATHS)
            self._backup_obstructions(target, backup, moved)
            # Edits have been backed up. --keep still protects runtime data and any
            # edits made concurrently, unlike a destructive reset --hard / clean.
            applied = True
            self._git("reset", "--keep", target, timeout=60)
            requirements_changed = self._git(
                "diff", "--name-only", previous, target, "--", "app/requirements.txt").stdout.strip()
            if requirements_changed and (self.root / "app/requirements.txt").is_file():
                self._set(message="Installing updated app dependencies…")
                dependencies = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                     "--no-input", "-r", str(self.root / "app/requirements.txt")],
                    cwd=self.root, capture_output=True, timeout=600, creationflags=NO_WINDOW)
                if dependencies.returncode:
                    raise UpdateError("App dependencies could not be installed. The previous code "
                                      "version has been restored. Run setup.bat before retrying.")
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
                    return
            try:
                if edits_cleared:
                    self._git("stash", "apply", "--index", edits, timeout=60)
                for path, saved in reversed(moved):
                    if path.exists() or path.is_symlink() or not path.parent.resolve().is_relative_to(self.root):
                        raise UpdateError("A file changed during update recovery.")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    saved.rename(path)
            except Exception:
                message = "The previous code was restored, but some local edits need recovery from app/data/update-backups and the local Git stash."
            self._set(phase="error", can_install=False, message=message)
        finally:
            if not restarting:
                self.resume_jobs()
            self._operation.release()
