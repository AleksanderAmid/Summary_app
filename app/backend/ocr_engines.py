"""Local Swedish OCR adapters; no document text or images leave this machine."""
from __future__ import annotations
import atexit
import base64
import csv
from functools import lru_cache
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

APP_ROOT = Path(__file__).resolve().parent.parent
OCR_DIR = APP_ROOT / "data" / "ocr"
MODEL_NAMES = ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@lru_cache(maxsize=1)
def tesseract_config():
    candidates = [os.environ.get("SMARTDOC_TESSERACT"), shutil.which("tesseract")]
    if os.name == "nt":
        candidates += [str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe"),
                       str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe")]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        data_dirs = [os.environ.get("SMARTDOC_TESSDATA"), str(OCR_DIR / "tessdata"),
                     os.environ.get("TESSDATA_PREFIX"), str(Path(candidate).parent / "tessdata"),
                     "/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata"]
        for data_dir in data_dirs:
            if data_dir and (Path(data_dir) / "swe.traineddata").is_file():
                languages = "swe+eng" if (Path(data_dir) / "eng.traineddata").is_file() else "swe"
                return candidate, data_dir, languages
    return None


@lru_cache(maxsize=1)
def paddle_model_dirs():
    roots = [Path(os.environ.get("SMARTDOC_OCR_MODELS", str(OCR_DIR / "models"))),
             Path.home() / ".paddlex/official_models"]
    for root in roots:
        paths = [root / name for name in MODEL_NAMES]
        if all((p / "inference.json").is_file() and (p / "inference.pdiparams").is_file() for p in paths):
            return tuple(str(p) for p in paths)
    return None


def status():
    # Do not import Paddle or contact model hosts during health checks.
    return {"tesseract": bool(tesseract_config()),
            "paddleocr": bool(paddle_model_dirs() and importlib.util.find_spec("paddleocr")
                              and importlib.util.find_spec("paddle")),
            "language": "Swedish", "mode": "balanced"}


def _row_text(words):
    text = ""
    previous = None
    for word in sorted(words, key=lambda w: w["box"][0]):
        gap = ""
        if previous is not None:
            char_width = max(3, (previous["box"][2]-previous["box"][0]) / max(1, len(previous["text"])))
            gap = "\t" if word["box"][0] - previous["box"][2] > char_width * 3 else " "
        text += gap + word["text"]
        previous = word
    return text


def tesseract(png: bytes, psm=3, timeout=45):
    config = tesseract_config()
    if config is None:
        raise RuntimeError("Swedish Tesseract data is unavailable. Run setup_ocr.ps1.")
    executable, data_dir, languages = config
    started = time.perf_counter()
    command = [executable, "stdin", "stdout", "--tessdata-dir", data_dir,
               "-l", languages, "--psm", str(psm), "--dpi", "300", "-c", "tessedit_create_tsv=1"]
    result = subprocess.run(command, input=png, capture_output=True, timeout=timeout,
                            creationflags=NO_WINDOW, env=dict(os.environ, OMP_THREAD_LIMIT="2"))
    if result.returncode:
        raise RuntimeError("Tesseract could not read this page.")
    words, groups = [], {}
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        try:
            x, y, w, h = [int(row[key]) for key in ("left", "top", "width", "height")]
            word = {"text": row["text"].strip(), "confidence": float(row["conf"]), "box": [x, y, x+w, y+h]}
        except (ValueError, TypeError, KeyError):
            continue
        words.append(word)
        key = tuple(row.get(k) for k in ("block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append(word)
    lines = []
    for group in groups.values():
        lines.append({"text": _row_text(group),
                      "box": [min(w["box"][0] for w in group), min(w["box"][1] for w in group),
                              max(w["box"][2] for w in group), max(w["box"][3] for w in group)],
                      "confidence": sum(w["confidence"] for w in group)/len(group)})
    text = "\n".join(line["text"] for line in lines)
    weight = sum(len(w["text"]) for w in words)
    confidence = sum(len(w["text"])*w["confidence"] for w in words)/max(1, weight)
    return {"engine": "tesseract", "text": text, "lines": lines, "words": words,
            "confidence": confidence, "seconds": time.perf_counter()-started}


class PaddleWorker:
    """One reusable isolated engine. Lock protects its predictor; timeout kills only this worker."""
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.responses = None
        self.unavailable_until = 0

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def _start(self):
        paths = paddle_model_dirs()
        if paths is None or not status()["paddleocr"]:
            raise RuntimeError("PaddleOCR's local models are unavailable. Run setup_ocr.ps1.")
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        env = dict(os.environ, PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True",
                   HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", OMP_NUM_THREADS="4",
                   SMARTDOC_PADDLE_DET=paths[0], SMARTDOC_PADDLE_REC=paths[1],
                   PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        self.process = subprocess.Popen([str(executable), "-B", str(Path(__file__).with_name("ocr_worker.py"))],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env=env, creationflags=NO_WINDOW)
        responses = self.responses = queue.Queue()
        stream = self.process.stdout
        def read_responses():
            try:
                for line in stream:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict) and "ok" in value:
                            responses.put(value)
                    except (ValueError, UnicodeDecodeError):
                        continue
            finally:
                responses.put({"ok": False})
        threading.Thread(target=read_responses, daemon=True, name="ocr-worker-output").start()

    def predict(self, png, timeout=180):
        with self.lock:
            if time.monotonic() < self.unavailable_until:
                raise RuntimeError("PaddleOCR is temporarily unavailable after a failed request.")
            try:
                if self.process is None or self.process.poll() is not None:
                    self.close()
                    self._start()
                request = json.dumps({"image": base64.b64encode(png).decode("ascii")}).encode()+b"\n"
                self.process.stdin.write(request)
                self.process.stdin.flush()
                value = self.responses.get(timeout=timeout)
                if not value.get("ok"):
                    raise RuntimeError("PaddleOCR could not complete this page.")
                return value["result"]
            except (OSError, queue.Empty, RuntimeError):
                self.close()
                self.unavailable_until = time.monotonic() + 60
                raise RuntimeError("PaddleOCR failed or timed out on this page; another local reader will be used.") from None


PADDLE = PaddleWorker()
atexit.register(PADDLE.close)


def paddle(png: bytes):
    return PADDLE.predict(png)