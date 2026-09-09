"""Transcribe, pseudonymise, summarise and restore identifiers in a local job.

Jobs run on a background thread; the frontend polls /api/jobs/<id>.
"""
from __future__ import annotations

import copy
import threading
import time
import traceback
from pathlib import Path

import anonymization
from privacy import MappingVault
import history
import summarization
import transcription

APP_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = APP_ROOT / "data" / "uploads"

STAGE_DEFS = [
    ("transcribe", "Transcribing file to text..."),
    ("anonymize", "Pseudonymising documents..."),
    ("summarize", "Summarizing..."),
    ("deanonymize", "Restoring summary identifiers..."),
    ("complete", "Summary complete"),
]

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_updates_paused = False


def _new_stages() -> list[dict]:
    return [{"key": k, "label": l, "status": "pending", "detail": "", "seconds": None}
            for k, l in STAGE_DEFS]


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return copy.deepcopy(job) if job else None


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


def active_job_count() -> int:
    with _jobs_lock:
        return sum(job["status"] == "running" for job in _jobs.values())


def pause_for_update() -> None:
    global _updates_paused
    with _jobs_lock:
        if any(job["status"] == "running" for job in _jobs.values()):
            raise RuntimeError("A summary is still running. Install the update when it finishes.")
        if _updates_paused:
            raise RuntimeError("An update is already being installed.")
        _updates_paused = True


def resume_after_update() -> None:
    global _updates_paused
    with _jobs_lock:
        _updates_paused = False


def start_job(payload: dict) -> str:
    """Accept text, a legacy single file, or an ordered list of files."""
    job_id = history.new_id()
    with _jobs_lock:
        if _updates_paused:
            raise RuntimeError("An update is being installed. Please try again after the app restarts.")
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
    if payload.get("files"):
        files = payload["files"]
        first = Path(files[0]["filename"]).stem
        return first if len(files) == 1 else f"{first} + {len(files) - 1} documents"
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
    transcription_warnings = []
    files = payload.get("files")
    if files is None and payload.get("filename"):
        files = [payload]
    if files:
        _set_stage(job_id, "transcribe", status="active")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        documents, texts = [], []
        for index, document in enumerate(files, 1):
            safe_name = Path(document["filename"]).name
            label = f"Document {index} of {len(files)}: {safe_name}"
            _set_stage(job_id, "transcribe", detail=f"{label} — reading")
            # The index keeps equal filenames from different folders distinct.
            upload_path = UPLOAD_DIR / f"{job_id}_{index:02d}_{safe_name}"
            upload_path.write_bytes(document["content"])

            def cb(detail: str, label: str = label) -> None:
                _set_stage(job_id, "transcribe", detail=f"{label} — {detail}")

            trans = transcription.transcribe_file(upload_path, cb, mode=payload.get("transcription_mode", "balanced"))
            transcription_warnings.extend(f"Document {index}, {warning}" for warning in trans.get("warnings", []))
            text = trans["full_text"].strip()
            if not text:
                raise RuntimeError(f"{safe_name}: no text could be extracted. "
                                   "Remove or replace this document and try again.")
            documents.append({"filename": safe_name, "method": trans["method"],
                              "pages": trans["pages"], "chars": len(text),
                              "words": len(text.split()), "seconds": trans.get("seconds"),
                              "review_required": trans.get("review_required", False)})
            texts.append(f"--- Document {index} ---\n{text}")
        source_text = "\n\n".join(texts)
        input_info = {"type": "file" if len(files) == 1 else "files",
                      "filename": documents[0]["filename"] if len(files) == 1 else None,
                      "files": documents, "document_count": len(documents)}
        if len(files) == 1:
            input_info.update(method=documents[0]["method"], pages=documents[0]["pages"])
        _set_stage(job_id, "transcribe", status="done",
                   detail=f"{len(files)} document(s), {len(source_text)} characters extracted",
                   seconds=time.perf_counter() - t0)
    else:
        source_text = (payload.get("text") or "").strip()
        if not source_text:
            raise RuntimeError("No input text provided.")
        _set_stage(job_id, "transcribe", status="skipped",
                   detail="Pasted text — no transcription needed",
                   seconds=time.perf_counter() - t0)
        input_info = {"type": "text"}

    input_info["chars"] = len(source_text)
    input_info["words"] = len(source_text.split())

    # ---------------- Stage 2: pseudonymisation ----------------
    t0 = time.perf_counter()
    _set_stage(job_id, "anonymize", status="active")
    anon = anonymization.run_anonymization_stage(
        source_text, lambda detail: _set_stage(job_id, "anonymize", detail=detail),
        model=summarization.SUMMARIZER_MODEL,
        additional=payload.get("additional_identifiers", []))
    n_phi = sum(anon["phi_hits"].values())
    _set_stage(job_id, "anonymize", status="done",
               detail=f"{n_phi} identifier occurrences replaced; review before sharing",
               seconds=time.perf_counter() - t0)

    # No unencrypted codebook is persisted or returned through the API.
    # Context cleanup removes ciphertext on success and on every error path.
    with MappingVault(job_id) as vault:
        mapping = anon.pop("mapping")
        known_tokens = {entry["token"] for entry in mapping.values()}
        vault.seal(mapping)
        del mapping
        t0 = time.perf_counter()
        _set_stage(job_id, "summarize", status="active", detail="Preparing source-linked summary")
        gen = summarization.summarize(
            anon["text"], lambda detail: _set_stage(job_id, "summarize", detail=detail))
        if not gen["summary"]:
            raise RuntimeError("The model returned an empty summary.")
        # Existing placeholders in already-pseudonymised input are allowed too.
        source_tokens = set(anonymization.IDENTIFIER_PLACEHOLDER_RE.findall(anon["text"]))
        generated_text = gen["summary"] + "\n" + "\n".join(gen.get("uncertainties", []))
        unknown = set(anonymization.IDENTIFIER_PLACEHOLDER_RE.findall(generated_text)) - source_tokens - known_tokens
        if unknown:
            raise RuntimeError("The model changed an identifier placeholder. Retry the summary.")
        tel = gen["telemetry"]
        _set_stage(job_id, "summarize", status="done",
                   detail=f"{tel.get('eval_count', '?')} tokens; source references checked",
                   seconds=time.perf_counter() - t0)

        t0 = time.perf_counter()
        _set_stage(job_id, "deanonymize", status="active")
        restored = vault.restore(gen["summary"])
        restored_uncertainties = [vault.restore(item) for item in gen.get("uncertainties", [])]
        _set_stage(job_id, "deanonymize", status="done",
                   detail="Original identifiers restored in summary; temporary mapping removed",
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
        "transcription": {"mode": payload.get("transcription_mode", "balanced"),
                          "warnings": transcription_warnings, "review_required": bool(transcription_warnings)},
        "source_text": anon["text"],
        "pseudonymised_text": anon["text"],
        "evidence": gen.get("evidence", summarization.evidence_units(anon["text"])),
        "summary_pseudonymised": gen["summary"],
        "summary": restored,
        "uncertainties": restored_uncertainties,
        "quality_warnings": gen.get("quality_warnings", []),
        "pseudonymisation": {key: value for key, value in anon.items() if key != "text"},
        "privacy": {"mapping_encrypted_at_rest": True, "mapping_retained": False,
                    "summary_identifiers": "restored"},
        "phi_screen": anon["phi_hits"],
        "telemetry": tel,
        "stages": stages_snapshot,
    }
    history.save_record(record)
    return record
