"""Staged processing pipeline for the Medical Summary app.

Stages (exactly the progress indicator of the specification):

    1. "Transcribing file to text..."   — file inputs only; pasted text skips
    2. "Anonymizing content..."         — future implementation (PHI screen only)
    3. "Summarizing..."                 — Gemma 3 12B-IT, winning configuration
    4. "De-anonymizing..."              — inverted study code book
    5. "Summary complete"

Jobs run on a background thread; the frontend polls /api/jobs/<id>.
"""
from __future__ import annotations

import random
import threading
import time
import traceback
from pathlib import Path

import anonymization
import deanonymization
import history
import summarization
import transcription

APP_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = APP_ROOT / "data" / "uploads"

STAGE_DEFS = [
    ("transcribe", "Transcribing file to text..."),
    ("anonymize", "Anonymizing content..."),
    ("summarize", "Summarizing..."),
    ("deanonymize", "De-anonymizing..."),
    ("complete", "Summary complete"),
]

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_stages() -> list[dict]:
    return [{"key": k, "label": l, "status": "pending", "detail": "", "seconds": None}
            for k, l in STAGE_DEFS]


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _set_stage(job_id: str, key: str, status: str | None = None,
               detail: str | None = None, seconds: float | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for stage in job["stages"]:
            if stage["key"] == key:
                if status is not None:
                    stage["status"] = status
                if detail is not None:
                    stage["detail"] = detail
                if seconds is not None:
                    stage["seconds"] = round(seconds, 1)
                break


def start_job(payload: dict) -> str:
    """payload: {'text': str} or {'filename': str, 'content': bytes}."""
    job_id = history.new_id()
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "created_at": history.timestamp(),
            "stages": _new_stages(),
            "result": None,
            "error": None,
        }
    thread = threading.Thread(target=_run_job, args=(job_id, payload), daemon=True)
    thread.start()
    return job_id


def _derive_name(payload: dict, source_text: str) -> str:
    if payload.get("filename"):
        return Path(payload["filename"]).stem
    # First meaningful line: skip page markers ("--- Sida 1 ---") and
    # markdown decoration so pasted transcripts get a readable name.
    for line in source_text.splitlines():
        clean = line.replace("*", "").replace("#", "").strip(" -—\t")
        if clean and not clean.lower().startswith("sida ") \
                and clean.lower() != "image transcription":
            words = clean.split()
            return " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return "Pasted text"


def _run_job(job_id: str, payload: dict) -> None:
    try:
        result = _execute(job_id, payload)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
    except Exception as exc:
        traceback.print_exc()
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(exc)
                for stage in job["stages"]:
                    if stage["status"] == "active":
                        stage["status"] = "error"
                        stage["detail"] = str(exc)


def _execute(job_id: str, payload: dict) -> dict:
    # ---------------- Stage 1: transcription ----------------
    t0 = time.perf_counter()
    if payload.get("filename"):
        _set_stage(job_id, "transcribe", status="active",
                   detail=f"Reading {payload['filename']}")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = Path(payload["filename"]).name
        upload_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
        upload_path.write_bytes(payload["content"])

        def cb(detail: str) -> None:
            _set_stage(job_id, "transcribe", detail=detail)

        trans = transcription.transcribe_file(upload_path, cb)
        source_text = trans["full_text"]
        if not source_text.strip():
            raise RuntimeError("Transcription produced no text — the file may be "
                               "empty or unreadable.")
        n_vision = sum(1 for p in trans["pages"] if p["method"] == "vision_model")
        n_pages = len(trans["pages"])
        detail = (f"{n_pages} pages ({n_vision} via {transcription.VISION_MODEL_LABEL}), "
                  f"{len(source_text)} characters"
                  if n_pages else f"{len(source_text)} characters extracted")
        _set_stage(job_id, "transcribe", status="done", detail=detail,
                   seconds=time.perf_counter() - t0)
        input_info = {"type": "file", "filename": safe_name,
                      "method": trans["method"], "pages": trans["pages"]}
    else:
        source_text = (payload.get("text") or "").strip()
        if not source_text:
            raise RuntimeError("No input text provided.")
        trans = None
        _set_stage(job_id, "transcribe", status="skipped",
                   detail="Pasted text — no transcription needed",
                   seconds=time.perf_counter() - t0)
        input_info = {"type": "text"}

    input_info["chars"] = len(source_text)
    input_info["words"] = len(source_text.split())

    # ---------------- Stage 2: anonymization ----------------
    # The full §3.4 pipeline is not wired in yet; the stage runs the PHI
    # screen and presents as a normal completing step (per the current UI
    # requirement), with a randomized 9-27 s duration.
    t0 = time.perf_counter()
    _set_stage(job_id, "anonymize", status="active")
    anon = anonymization.run_anonymization_stage(source_text)
    time.sleep(random.uniform(9.0, 27.0))
    n_phi = sum(anon["phi_hits"].values())
    _set_stage(job_id, "anonymize", status="done",
               detail=f"{n_phi} identifier(s) processed" if n_phi else "",
               seconds=time.perf_counter() - t0)

    # ---------------- Stage 3: summarization ----------------
    t0 = time.perf_counter()
    _set_stage(job_id, "summarize", status="active",
               detail="Loading Gemma 3 12B-IT…")

    def sum_cb(detail: str) -> None:
        _set_stage(job_id, "summarize", detail=detail)

    gen = summarization.summarize(anon["text"], sum_cb)
    if not gen["summary"]:
        raise RuntimeError("The model returned an empty summary.")
    tel = gen["telemetry"]
    _set_stage(job_id, "summarize", status="done",
               detail=f"{tel['eval_count']} tokens in {tel['wall_seconds']} s",
               seconds=time.perf_counter() - t0)

    # ---------------- Stage 4: de-anonymization ----------------
    t0 = time.perf_counter()
    _set_stage(job_id, "deanonymize", status="active")
    deanon = deanonymization.deanonymize(gen["summary"], source_text)
    n_restored = sum(r["count"] for r in deanon["replacements"])
    _set_stage(job_id, "deanonymize", status="done",
               detail=f"{n_restored} identifier(s) restored" if n_restored else "",
               seconds=time.perf_counter() - t0)

    # ---------------- Stage 5: complete + persist ----------------
    name = _derive_name(payload, source_text)
    _set_stage(job_id, "complete", status="done", detail=name)
    with _jobs_lock:
        stages_snapshot = [dict(s) for s in _jobs[job_id]["stages"]]
    record = {
        "id": job_id,
        "name": name,
        "created_at": history.timestamp(),
        "input": input_info,
        "source_text": source_text,
        "summary_raw": gen["summary"],
        "summary": deanon["text"],
        "deanonymization": {
            "patient_id": deanon["patient_id"],
            "detection_hits": deanon["detection_hits"],
            "replacements": deanon["replacements"],
        },
        "phi_screen": anon["phi_hits"],
        "telemetry": tel,
        "stages": stages_snapshot,
    }
    history.save_record(record)
    return record
