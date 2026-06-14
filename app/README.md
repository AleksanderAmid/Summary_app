# SmartDoc — Medical Summary App

Interactive application wrapping the winning configuration from the thesis
model battle (§4.4), with the full processing pipeline of the specification:
file transcription → (anonymization, future) → summarization →
de-anonymization, with staged progress and a persistent run history.

## The study configuration it integrates

| Component | Choice | Source |
|---|---|---|
| Summarizer | **Gemma 3 12B-IT** (`gemma3:12b-it-q4_K_M`) — best BERTScore-F1 0.672 (inter-reviewer ceiling 0.609), ROUGE-L 0.221 | `report/tables/master_comparison.tex`, §4.4 |
| Methodology | **Prompt engineering, few-shot variant** — won Gemma's PE cell on 3/5 evaluation patients (CoT 2/5, clinician-brief 0/5) | `scripts/eval/reports/per_run_metrics.json` |
| Reference panel | **All 25 physician summaries** (5 patients × 5 reviewing physicians) embedded in the system prompt | `data/ground_truth/_master_ground_truth.json` |
| Generation | temperature 0.0 · top-p 1.0 · num_predict 512 · seed 42 (identical to `scripts/run/run_battle.py`); `num_ctx` sized dynamically (8k–32k) | §3.6 protocol |
| Transcription | **Gemma 3 12B-IT** (`gemma3:12b-it-q4_K_M`, multimodal) — same model as the summarizer, so no model swap between stages; `/api/generate`, retry strategy + thinking-contamination sanitization from `src/extraction/transcribe.py`, 200 DPI. *Deviation:* the study's Phase-1 corpus was transcribed with Qwen3-VL-8B (chosen for medical content fidelity); set `VISION_MODEL = "qwen3-vl:8b"` in `backend/transcription.py` to restore it | `src/extraction/transcribe.py` |
| De-anonymization | Inversion of the study's per-patient code books `data/deidentified/<id>/_mapping.json` (real→fake), auto-detected per document | §3.4 pipeline output |

> Note: the published 0.672 score was measured with the original two-exemplar
> few-shot template. Embedding the full 25-summary panel is this
> application's configuration (per the app specification), not the evaluated
> one.

## Requirements

The `app/` folder is **self-contained and portable**: copy it to any machine
or user account and it runs. The study data it needs (the 25 physician
reference summaries and the 13 de-anonymization code books) is bundled in
`study_data/`. On the target machine you need:

- **Python 3.10+** ([python.org](https://www.python.org/downloads/), check
  "Add python.exe to PATH" during installation)
- **Ollama** ([ollama.com](https://ollama.com/download)) with the model:
  `ollama pull gemma3:12b-it-q4_K_M` (~8 GB)
- *(optional)* **PyMuPDF** for PDF files: `python -m pip install pymupdf`.
  The launcher installs it automatically when missing; without it the app
  still runs and PDF uploads show an install hint. Everything else is the
  Python standard library.

## Setup on a new device

Double-click **`setup.bat`** once. It checks for and installs everything in
order — Python 3.10+, Ollama, the `gemma3:12b-it-q4_K_M` model (~8 GB pull),
and PyMuPDF — using winget when available and the official installers
otherwise, skipping whatever is already present. Safe to re-run at any time.

## Run

Double-click `run_app.bat` (it finds Python, offers to install PyMuPDF, and
opens the browser), or manually from inside the app folder:

```powershell
python backend\server.py
```

The UI opens at **http://localhost:8765**.

## Using the app

1. **Upload a file** (primary) — PDF, DOCX, TXT, PNG/JPG, or one of the
   study's own transcription JSON files — or **paste text**.
2. The progress indicator walks through the stages of the specification:
   - *Transcribing file to text...* — native text layer when available;
     scanned pages go through Gemma 3 12B-IT vision (~1–2 min per page)
   - *Anonymizing content...* — presented as a normal completing stage in
     the UI; under the hood only the regex PHI screen runs today (the full
     §3.4 pipeline is the integration point in `backend/anonymization.py`)
   - *Summarizing...* — Gemma 3 12B-IT with the 25 doctor summaries in the
     system prompt
   - *De-anonymizing...* — restores real identifiers via the matched
     code book, or passes through unchanged if none matches
   - *Summary complete*
3. The final summary appears with copy/download buttons, plus collapsible
   panels showing the exact model input and run telemetry. (The full
   de-anonymization replacement list is still stored in each history JSON
   under `deanonymization`, it is just not displayed.)
4. Every run is saved to the **history sidebar** (name + date). Click to
   re-open, double-click to rename, ✕ to delete. History lives in
   `app/data/history/` as plain JSON.

## Folder structure

```
app/
├── setup.bat / setup.ps1  # one-time installer: Python, Ollama, model, PyMuPDF
├── run_app.bat            # one-click launcher (any Python 3.10+ on PATH)
├── requirements.txt       # pymupdf only (optional, for PDF support)
├── backend/
│   ├── server.py          # stdlib HTTP server + REST API (entry point)
│   ├── pipeline.py        # staged job orchestration (background threads)
│   ├── transcription.py   # file → text (port of the Phase-1 pipeline)
│   ├── summarization.py   # winning Gemma config + system-prompt builder
│   ├── anonymization.py   # future stage (PHI screen today)
│   ├── deanonymization.py # code-book inversion + patient auto-detection
│   ├── ollama_client.py   # stdlib HTTP client for the Ollama API
│   └── history.py         # JSON history store
├── frontend/
│   ├── index.html         # single-page UI (cream/serif theme)
│   ├── style.css
│   └── app.js
├── study_data/            # bundled copies of the study artifacts
│   ├── ground_truth.json  #   25 physician summaries (from data/ground_truth/)
│   └── codebooks/         #   13 per-patient code books (from data/deidentified/)
├── tests/
│   └── test_deanonymization.py   # regression tests for the code-book inversion
└── data/
    ├── history/           # one JSON per completed run
    └── uploads/           # uploaded source files
```

Note: `study_data/` holds **copies** made from `data/ground_truth/` and
`data/deidentified/` — if those study artifacts ever change, refresh the
copies. The code books contain real↔fake identifier pairs, so the folder
must be handled with the same care as the study data itself.

## Safety

This is a research artefact. Generated summaries must be reviewed by a
clinician before any clinical use — see §4.6 of the thesis for omission and
hallucination findings. All inference is local (Ollama on this machine); no
patient text leaves the workstation. Note that `app/data/` will contain
patient text and restored identifiers once the app is used on study
documents — treat it with the same care as `data/`.
