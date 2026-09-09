"""Explicit setup-only downloads. Normal OCR requires local models and runs offline."""
import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
from pathlib import Path
import ocr_engines


def main():
    if ocr_engines.paddle_model_dirs():
        print("PaddleOCR recognition models are already present.")
        return
    from paddleocr import PaddleOCR
    print("Downloading Swedish-capable OCR models. This may take several minutes.", flush=True)
    PaddleOCR(lang="sv", ocr_version="PP-OCRv6", use_doc_orientation_classify=False,
              use_doc_unwarping=False, use_textline_orientation=False, device="cpu",
              cpu_threads=4, enable_mkldnn=False)
    print("OCR models are ready. Restart SmartDoc.")


if __name__ == "__main__":
    main()