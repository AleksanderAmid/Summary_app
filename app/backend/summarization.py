"""Summarization for the Medical Summary app.

Integrates the winning configuration from the thesis model battle (§4.4):

    Model:        Gemma 3 12B-IT, Ollama tag ``gemma3:12b-it-q4_K_M``
                  (best BERTScore-F1 in the master comparison: 0.672;
                  inter-reviewer ceiling 0.609)
    Methodology:  prompt engineering, few-shot variant — the variant that won
                  Gemma's prompt-engineering cell on 3 of 5 evaluation
                  patients (CoT won the other 2; clinician-brief none)
    Generation:   temperature 0.0, top-p 1.0, num_predict 512, seed 42 —
                  identical to scripts/run/run_battle.py

Per the application specification, the few-shot methodology is extended so
that ALL 25 physician reference summaries (5 patients x 5 reviewing
physicians, data/ground_truth/_master_ground_truth.json) are embedded in the
system prompt as style/quality references. Note that the published 0.672
score was measured with the original two-exemplar template; embedding the
full panel is the application's configuration, not the evaluated one.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

import requests

OLLAMA_HOST = "http://localhost:11434"
SUMMARIZER_MODEL = "gemma3:12b-it-q4_K_M"

# Generation hyperparameters — identical to the thesis evaluation protocol.
GENERATION_OPTIONS = {
    "temperature": 0.0,
    "top_p": 1.0,
    "num_predict": 512,
    "seed": 42,
}

APP_ROOT = Path(__file__).resolve().parent.parent          # .../app
REPO_ROOT = APP_ROOT.parent                                 # .../Examensarbetet
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth" / "_master_ground_truth.json"

ProgressCb = Callable[[str], None]


# ------------------------------------------------------------------
# System prompt: few-shot role + all 25 physician reference summaries
# ------------------------------------------------------------------

def load_doctor_summaries() -> list[dict]:
    """Load the 25 physician-written reference summaries from the study."""
    data = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    entries = []
    for patient in data.get("patients", []):
        pid = patient.get("patient_id")
        for s in patient.get("summaries", []):
            entries.append({
                "patient_id": pid,
                "doctor_id": s.get("doctor_id"),
                "doctor_name": s.get("doctor_name"),
                "experience_years": s.get("experience_years"),
                "text": (s.get("text") or "").strip(),
            })
    return entries


def build_system_prompt() -> str:
    """Few-shot system prompt with the full physician reference panel embedded.

    The role line and the task wording are taken verbatim from the winning
    few-shot template (scripts/run/prompts/prompt_engineering_few_shot.txt);
    the two static exemplars of that template are replaced by the complete
    reference panel, as required by the application specification, and one
    anti-Markdown format line is appended (an app-level addition).
    """
    summaries = load_doctor_summaries()

    parts = [
        "## SYSTEM ROLE",
        "Du är en erfaren svensk allmänläkare som sammanfattar journaler för kollegor.",
        "",
        "## REFERENSSAMMANFATTNINGAR",
        "Nedan följer samtliga referenssammanfattningar från studiens läkarpanel: "
        "fem granskande läkare som var och en sammanfattat fem patientjournaler "
        "(25 sammanfattningar totalt). Använd dem som exempel på hur en korrekt, "
        "koncis klinisk överlämningssammanfattning ska se ut — i stil, längd, "
        "innehållsurval och medicinskt språk.",
        "",
    ]

    current_pid = None
    for e in summaries:
        if e["patient_id"] != current_pid:
            current_pid = e["patient_id"]
            parts.append(f"### EXEMPELFALL {current_pid}")
            parts.append("")
        parts.append(
            f"Läkare {e['doctor_name']} ({e['experience_years']} års erfarenhet) "
            f"sammanfattade fall {e['patient_id']} så här:"
        )
        parts.append(e["text"])
        parts.append("")

    # Task wording verbatim from the winning few-shot template ("nedanstående
    # journal" refers to the ## PATIENTJOURNAL section sent in the user turn).
    # The final format line is an app-level addition: with 25 structured
    # reference summaries in context the model otherwise tends to emit
    # Markdown headers, which the battle's two-exemplar prompt never did.
    parts += [
        "## UPPGIFT",
        "Skriv på samma sätt en sammanfattning av nedanstående journal. Skriv ren "
        "löpande svensk text. Hitta inte på något som inte står i journalen.",
        "Svara med endast sammanfattningstexten, utan Markdown-formatering "
        "(ingen rubrik, inga ** eller ##).",
    ]
    return "\n".join(parts)


def build_user_prompt(source_text: str) -> str:
    """User-turn prompt — mirrors the tail of the winning few-shot template."""
    return f"## PATIENTJOURNAL\n{source_text}\n\n## SAMMANFATTNING\n"


# ------------------------------------------------------------------
# Generation
# ------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    # ~3 chars/token is a safe lower bound for Swedish clinical text on
    # SentencePiece-style vocabularies; overestimating num_ctx is harmless
    # apart from KV-cache memory.
    return len(text) // 3 + 64


def choose_num_ctx(system_prompt: str, user_prompt: str) -> tuple[int, bool]:
    """Pick a context window covering prompt + generation, capped at 32k."""
    needed = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt) \
        + GENERATION_OPTIONS["num_predict"] + 256
    for size in (8192, 16384, 24576, 32768):
        if needed <= size:
            return size, False
    return 32768, True  # may truncate — surfaced to the caller


def summarize(source_text: str, progress: ProgressCb | None = None) -> dict:
    """Generate the clinical summary with the winning study configuration."""
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(source_text)
    num_ctx, may_truncate = choose_num_ctx(system_prompt, user_prompt)

    if progress:
        n_ref = len(load_doctor_summaries())
        progress(f"Gemma 3 12B-IT — {n_ref} physician reference summaries in the "
                 f"system prompt, context window {num_ctx} tokens")

    options = dict(GENERATION_OPTIONS)
    options["num_ctx"] = num_ctx
    # Deviation from the battle protocol (single rendered prompt string): the
    # reference panel goes in the dedicated system field, as the application
    # specification requires the doctor summaries to live in the system
    # prompt. Ollama's Gemma template prepends system to the first user turn.
    payload = {
        "model": SUMMARIZER_MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": options,
        # run_battle.py sent think=False on /api/generate for every model in
        # the battle (0 failed runs), so this matches the evaluation protocol.
        "think": False,
    }
    t0 = time.perf_counter()
    r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=900)
    r.raise_for_status()
    data = r.json()
    summary = (data.get("response") or "").strip()
    # Defensive: drop a leading "## SAMMANFATTNING"-style header if the model
    # still echoes the prompt's section marker.
    summary = re.sub(r"^#{1,6}\s*SAMMANFATTNING\s*\n+", "", summary,
                     flags=re.IGNORECASE).strip()
    return {
        "summary": summary,
        "telemetry": {
            "model": SUMMARIZER_MODEL,
            "wall_seconds": round(time.perf_counter() - t0, 2),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "num_ctx": num_ctx,
            "may_truncate": may_truncate,
        },
    }


def summarizer_model_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        r.raise_for_status()
        tags = [m.get("name", "") for m in r.json().get("models", [])]
        return SUMMARIZER_MODEL in tags
    except Exception:
        return False


def ollama_alive() -> bool:
    try:
        return requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3).ok
    except Exception:
        return False
