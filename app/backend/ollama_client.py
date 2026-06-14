"""Minimal Ollama HTTP client — Python standard library only.

Replaces the `requests` dependency so the app folder runs on a bare Python
installation (portability requirement: the folder must be movable to another
device/account without a prepared virtual environment).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

OLLAMA_HOST = "http://localhost:11434"


def post_json(path: str, payload: dict, timeout: float = 600.0) -> dict:
    """POST JSON to the Ollama API and return the parsed JSON response."""
    req = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Ollama returned HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {OLLAMA_HOST} — is it "
                           f"running? ({e.reason})") from e


def get_json(path: str, timeout: float = 5.0) -> dict:
    """GET a JSON endpoint of the Ollama API."""
    with urllib.request.urlopen(f"{OLLAMA_HOST}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_model_tags(timeout: float = 3.0) -> list[str]:
    """Names of locally pulled models; empty list when Ollama is unreachable."""
    try:
        data = get_json("/api/tags", timeout=timeout)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def alive(timeout: float = 3.0) -> bool:
    try:
        get_json("/api/tags", timeout=timeout)
        return True
    except Exception:
        return False
