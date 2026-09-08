"""Shared hidden-process and log helpers for the launcher and restarts."""
import os
from pathlib import Path
import subprocess
import sys

APP_ROOT = Path(__file__).resolve().parent.parent


def open_log():
    directory = APP_ROOT / "data/logs"
    directory.mkdir(parents=True, exist_ok=True)
    return (directory / "smartdoc.log").open("a", encoding="utf-8", buffering=1)


def spawn_server(port=8765):
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if os.name == "nt" and windowless.exists():
        executable = windowless
    env = dict(os.environ, PORT=str(port), PYTHONDONTWRITEBYTECODE="1")
    with open_log() as log:
        return subprocess.Popen(
            [str(executable), "-B", str(APP_ROOT / "backend/server.py")],
            cwd=APP_ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
