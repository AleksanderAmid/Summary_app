"""Open SmartDoc without a console, reusing an existing local server."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import webbrowser

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT / "backend"))
from runtime import open_log, spawn_server


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return json.load(response).get("app") == "SmartDoc"
    except Exception:
        return False


def show_error(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "SmartDoc", 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    port = int(os.environ.get("PORT", "8765"))
    if not health(port):
        process = spawn_server(port)
        deadline = time.monotonic() + 20
        while not health(port):
            if process.poll() is not None or time.monotonic() > deadline:
                # Another concurrent launch may have won the port.
                if health(port):
                    break
                show_error("SmartDoc could not start. Check app/data/logs/smartdoc.log "
                           "for details, or run setup.bat to check your installation.")
                return
            time.sleep(0.2)
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    with open_log() as log:
        sys.stdout = sys.stderr = log
        try:
            main()
        except Exception:
            import traceback
            traceback.print_exc()
            show_error("SmartDoc could not open. See app/data/logs/smartdoc.log.")
