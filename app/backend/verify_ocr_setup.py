"""Verify Swedish OCR using generated test text only, never patient documents."""
import argparse
import io
import os
import subprocess


def verify(tesseract, tessdata):
    from PIL import Image, ImageDraw, ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except OSError:
            pass
    else:
        font = ImageFont.load_default(size=40)
    page = Image.new("L", (800, 130), 255)
    ImageDraw.Draw(page).text((30, 35), "Svensk text: å ä ö 12345", font=font, fill=0)
    image = io.BytesIO()
    page.save(image, format="PNG")
    result = subprocess.run(
        [tesseract, "stdin", "stdout", "--tessdata-dir", tessdata, "-l", "swe", "--psm", "6"],
        input=image.getvalue(), capture_output=True, timeout=45,
        env=dict(os.environ, OMP_THREAD_LIMIT="2"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = result.stdout.decode("utf-8", "replace").lower()
    if result.returncode or not all(part in text for part in ("svensk", "å", "ä", "ö", "12345")):
        raise RuntimeError("Tesseract could not read the Swedish setup test. Check its installation and Swedish language data.")
    print("Swedish OCR verified: Tesseract can read Swedish letters and numbers.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tesseract", required=True)
    parser.add_argument("--tessdata", required=True)
    args = parser.parse_args()
    try:
        verify(args.tesseract, args.tessdata)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, str(exc) + "\n")
