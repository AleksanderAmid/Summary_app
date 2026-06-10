"""File -> text transcription for the Medical Summary app.

Faithful port of the production transcription pipeline from
src/extraction/transcribe.py (Phase 1 of the thesis):

    Vision model:  Qwen3-VL-8B  (Ollama tag ``qwen3-vl:8b``)
    Endpoint:      /api/generate  (avoids the content/thinking field split
                   that caused empty pages with /api/chat)
    Prompting:     5 retries with alternating short prompts + ``/no_think``
    Rendering:     PyMuPDF at 200 DPI (the DPI used for the 627-page corpus)
    Post-process:  <think> stripping + thinking-contamination sanitization
                   + repetition-loop truncation

Native-text PDF pages (>= 50 chars of embedded text) use the embedded text
directly so digital documents do not pay the ~95 s/page vision-model cost;
scanned pages go through the vision model exactly as in the study.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Callable

import requests

OLLAMA_HOST = "http://localhost:11434"
# Gemma 3 12B-IT is multimodal, so the same model handles transcription and
# summarization (no model swap between stages). NOTE: this deviates from the
# study, whose Phase-1 corpus was transcribed with Qwen3-VL-8B (chosen over
# Gemma 4 E4B for medical content fidelity). Set back to "qwen3-vl:8b" to
# restore the study's transcription model.
VISION_MODEL = "gemma3:12b-it-q4_K_M"
VISION_MODEL_LABEL = "Gemma 3 12B-IT"
RENDER_DPI = 200          # production value used for the full corpus
MIN_NATIVE_CHARS = 50     # below this a page is treated as scanned

ProgressCb = Callable[[str], None]


# ------------------------------------------------------------------
# Text sanitization (ported verbatim from src/extraction/transcribe.py)
# ------------------------------------------------------------------

def sanitize_thinking_contamination(text: str) -> str:
    """Remove any LLM thinking/reasoning patterns from transcribed text."""
    if not text:
        return text
    changed = True
    while changed:
        original = text
        thinking_patterns = [
            r'^(?:Got it|Okay|OK|Alright|Let me|I need to|I will|I\'ll|Sure|Here is|Here\'s|Let\'s)[^\n]*\n+',
            r'^(?:Jag ska|Jag behöver|Jag kommer|Låt mig|Här är)[^\n]*\n+',
            r'^(?:The (?:page|image|document|table|lab|report))[^\n]*\n+',
            r'^(?:This (?:page|image|document|table|lab|report))[^\n]*\n+',
            r'^(?:Looking at|I can see|I see|Now (?:I|let)|First|Starting)[^\n]*\n+',
        ]
        for pattern in thinking_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
        changed = text != original
    text = re.sub(r'\n+(?:I hope this|Let me know|Is there anything|That\'s all|Done)[^\n]*$',
                  '', text, flags=re.IGNORECASE).strip()
    text = truncate_repetition(text)
    return text


def truncate_repetition(text: str, max_repeats: int = 3) -> str:
    """Detect and truncate repetitive token loops in generated text."""
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        found_block = False
        for block_size in range(2, 6):
            if i + block_size * (max_repeats + 1) > len(lines):
                continue
            block = lines[i:i + block_size]
            block_text = '\n'.join(l.strip() for l in block)
            if not block_text.strip():
                continue
            repeats = 0
            j = i + block_size
            while j + block_size <= len(lines):
                next_block = '\n'.join(l.strip() for l in lines[j:j + block_size])
                if next_block == block_text:
                    repeats += 1
                    j += block_size
                else:
                    break
            if repeats >= max_repeats:
                result.extend(block)
                i = j
                found_block = True
                break
        if not found_block:
            result.append(lines[i])
            i += 1

    final = []
    repeat_count = 0
    prev_line = None
    for line in result:
        stripped = line.strip()
        if stripped == prev_line and stripped:
            repeat_count += 1
            if repeat_count >= max_repeats:
                continue
        else:
            repeat_count = 0
        prev_line = stripped
        final.append(line)
    return '\n'.join(final)


# ------------------------------------------------------------------
# Vision backend (ported from OllamaBackend in the study pipeline)
# ------------------------------------------------------------------

# Retry prompts; empty/truncated pages are non-deterministic, so the study
# retried each page up to 5 times with alternating prompts. The Gemma list
# mirrors the study pipeline's gemma branch (no /no_think token).
QWEN_RETRY_PROMPTS = [
    "Transcribe /no_think",
    "Transkribera /no_think",
    "Transcribe /no_think",
    "Skriv av all text /no_think",
    "Transcribe /no_think",
]
GEMMA_RETRY_PROMPTS = [
    "Transcribe all text from this image exactly as written.",
    "Transkribera all text exakt som den ser ut.",
    "Transcribe all text from this image exactly as written.",
    "Transcribe every word on this page. Output only the text.",
    "Transcribe all text from this image exactly as written.",
]


# An alphanumeric character repeated 20+ times means the vision model fell
# into a token loop mid-page (e.g. "2024-07777777..."), truncating the real
# content. The study retried only on EMPTY pages; the interactive app also
# retries on degenerate output. Separator runs ('-----', '....') are fine.
DEGENERATE_RUN = re.compile(r'([0-9A-Za-zÅÄÖåäö])\1{19,}')

# Vision models also nondeterministically truncate a page partway (observed:
# 268 chars where the same page/prompt normally yields ~900). A scanned
# clinical page virtually always exceeds this, so shorter output triggers the
# remaining retries; the longest candidate wins. Genuinely sparse pages just
# pay the extra retries and still return their full (short) text.
MIN_ACCEPTABLE_CHARS = 500


def transcribe_image_bytes(png_bytes: bytes, model: str = VISION_MODEL) -> str:
    """Send one page image through the vision model, with the study's retry loop."""
    img_base64 = base64.b64encode(png_bytes).decode("utf-8")
    candidates: list[str] = []
    prompts = GEMMA_RETRY_PROMPTS if "gemma" in model.lower() else QWEN_RETRY_PROMPTS
    for prompt in prompts:
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 16384},
        }
        if "qwen" in model.lower():
            payload["think"] = False
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=600)
        r.raise_for_status()
        content = r.json().get("response", "")
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        content = sanitize_thinking_contamination(content)
        if (len(content) >= MIN_ACCEPTABLE_CHARS
                and not DEGENERATE_RUN.search(content)):
            return content
        if content:
            candidates.append(content)
    if candidates:  # all retries short/degenerate — keep the longest clean one
        best = max(candidates, key=len)
        return DEGENERATE_RUN.sub(lambda m: m.group(1) * 3, best)
    return ""


def _page_to_png(page, dpi: int = RENDER_DPI) -> bytes:
    """Render a PyMuPDF page to PNG bytes at the study's DPI."""
    import fitz
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return pix.tobytes("png")


# ------------------------------------------------------------------
# File-type dispatch
# ------------------------------------------------------------------

def transcribe_pdf(path: Path, progress: ProgressCb) -> dict:
    import fitz
    doc = fitz.open(str(path))
    n = len(doc)
    pages = []
    t0 = time.perf_counter()
    for idx in range(n):
        page = doc[idx]
        native = page.get_text().strip()
        if len(native) >= MIN_NATIVE_CHARS:
            progress(f"Page {idx + 1} of {n} — embedded text layer")
            pages.append({"page_num": idx + 1, "method": "native", "text": native})
        else:
            progress(f"Page {idx + 1} of {n} — scanned page, "
                     f"running {VISION_MODEL_LABEL} (~1–2 min)")
            png = _page_to_png(page)
            text = transcribe_image_bytes(png)
            pages.append({"page_num": idx + 1, "method": "vision_model", "text": text})
    doc.close()
    full_text = "\n\n".join(f"--- Sida {p['page_num']} ---\n\n{p['text']}" for p in pages)
    return {
        "full_text": full_text,
        "pages": [{"page_num": p["page_num"], "method": p["method"], "chars": len(p["text"])}
                  for p in pages],
        "seconds": round(time.perf_counter() - t0, 1),
        "method": "pdf",
    }


def transcribe_docx(path: Path) -> dict:
    from docx import Document
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
            if row_text:
                paragraphs.append(row_text)
    text = "\n".join(paragraphs)
    return {"full_text": text, "pages": [], "seconds": 0.0, "method": "docx"}


def transcribe_image_file(path: Path, progress: ProgressCb) -> dict:
    progress(f"Running {VISION_MODEL_LABEL} on the image (~1–2 min)")
    t0 = time.perf_counter()
    data = path.read_bytes()
    if path.suffix.lower() not in (".png",):
        # normalise to PNG for the Ollama payload
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
    text = transcribe_image_bytes(data)
    return {"full_text": text,
            "pages": [{"page_num": 1, "method": "vision_model", "chars": len(text)}],
            "seconds": round(time.perf_counter() - t0, 1),
            "method": "image"}


def transcribe_study_json(path: Path) -> dict:
    """Accept the study's own transcription/de-identification JSON files directly."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if data.get("full_text"):
            text = data["full_text"]
        elif isinstance(data.get("pages"), list):
            text = "\n\n".join(
                f"--- Sida {p.get('page_num', i + 1)} ---\n\n{p.get('text', '')}"
                for i, p in enumerate(data["pages"])
            )
        else:
            raise ValueError("JSON file does not look like a study transcription file "
                             "(no 'full_text' or 'pages').")
    else:
        raise ValueError("Unsupported JSON structure.")
    return {"full_text": text, "pages": [], "seconds": 0.0, "method": "study_json"}


def transcribe_txt(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return {"full_text": text, "pages": [], "seconds": 0.0, "method": "txt"}


SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".json", ".png", ".jpg", ".jpeg")


def transcribe_file(path: Path, progress: ProgressCb) -> dict:
    """Dispatch on file extension. Returns {full_text, pages, seconds, method}."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return transcribe_pdf(path, progress)
    if ext == ".docx":
        return transcribe_docx(path)
    if ext == ".txt":
        return transcribe_txt(path)
    if ext == ".json":
        return transcribe_study_json(path)
    if ext in (".png", ".jpg", ".jpeg"):
        return transcribe_image_file(path, progress)
    raise ValueError(f"Unsupported file type '{ext}'. "
                     f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}")


def vision_model_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        r.raise_for_status()
        tags = [m.get("name", "") for m in r.json().get("models", [])]
        return VISION_MODEL in tags
    except Exception:
        return False
