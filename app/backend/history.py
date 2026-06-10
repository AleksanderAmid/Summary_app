"""Persistent run history for the Medical Summary app.

One JSON file per completed run in app/data/history/. The sidebar lists
{id, name, created_at}; the full record holds everything needed to re-open
a past run (summary, transcription, de-anonymization details, telemetry).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = APP_ROOT / "data" / "history"

_lock = threading.Lock()


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def save_record(record: dict) -> None:
    _ensure_dir()
    path = HISTORY_DIR / f"{record['id']}.json"
    with _lock:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def list_records() -> list[dict]:
    """Newest first; sidebar-sized fields only."""
    _ensure_dir()
    items = []
    for path in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append({
                "id": data.get("id", path.stem),
                "name": data.get("name", "Untitled"),
                "created_at": data.get("created_at", 0),
                "input_type": (data.get("input") or {}).get("type", ""),
            })
        except Exception:
            continue
    items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return items


def get_record(record_id: str) -> dict | None:
    path = HISTORY_DIR / f"{record_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_record(record_id: str) -> bool:
    path = HISTORY_DIR / f"{record_id}.json"
    with _lock:
        existed = path.is_file()
        path.unlink(missing_ok=True)
    return existed


def rename_record(record_id: str, name: str) -> bool:
    # Read-modify-write under the lock so a concurrent delete cannot
    # resurrect the record. File I/O is inlined because _lock is
    # non-reentrant and save_record acquires it internally.
    path = HISTORY_DIR / f"{record_id}.json"
    with _lock:
        if not path.is_file():
            return False
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        record["name"] = name.strip() or record.get("name", "Untitled")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return True


def timestamp() -> float:
    return time.time()
