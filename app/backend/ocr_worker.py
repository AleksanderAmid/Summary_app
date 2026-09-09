"""Private stdin/stdout worker; OCR pixels stay in memory, diagnostics contain no page text."""
import base64
import contextlib
import io
import json
import os
import sys
import time


def main():
    output = sys.stdout
    engine = None
    for request in sys.stdin:
        try:
            payload = json.loads(request)
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                import numpy as np
                from PIL import Image
                if engine is None:
                    from paddleocr import PaddleOCR
                    engine = PaddleOCR(
                        text_detection_model_name="PP-OCRv6_medium_det",
                        text_recognition_model_name="PP-OCRv6_medium_rec",
                        text_detection_model_dir=os.environ["SMARTDOC_PADDLE_DET"],
                        text_recognition_model_dir=os.environ["SMARTDOC_PADDLE_REC"],
                        use_doc_orientation_classify=False, use_doc_unwarping=False,
                        use_textline_orientation=False, device="cpu", cpu_threads=4,
                        enable_mkldnn=False, text_rec_score_thresh=0.0,
                        text_det_limit_side_len=1600, text_det_limit_type="max")
                with Image.open(io.BytesIO(base64.b64decode(payload["image"], validate=True))) as img:
                    array = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()
                predictions = list(engine.predict(array))
                lines = []
                for prediction in predictions:
                    texts = prediction.get("rec_texts", [])
                    scores = prediction.get("rec_scores", [])
                    polys = prediction.get("rec_polys", [])
                    for text, score, poly in zip(texts, scores, polys):
                        if not str(text).strip():
                            continue
                        points = np.asarray(poly)
                        lines.append({"text": str(text), "confidence": float(score)*100,
                                      "box": [float(points[:,0].min()), float(points[:,1].min()),
                                              float(points[:,0].max()), float(points[:,1].max())]})
            weight = sum(len(line["text"]) for line in lines)
            result = {"engine": "paddleocr", "text": "\n".join(line["text"] for line in lines),
                      "lines": lines, "words": lines,
                      "confidence": sum(len(line["text"])*line["confidence"] for line in lines)/max(1, weight),
                      "seconds": time.perf_counter()-started}
            response = {"ok": True, "result": result}
        except Exception:
            response = {"ok": False}
        output.write(json.dumps(response, ensure_ascii=True)+"\n")
        output.flush()


if __name__ == "__main__":
    main()