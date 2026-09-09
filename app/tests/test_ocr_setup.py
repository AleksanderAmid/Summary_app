"""Setup checks use synthetic OCR output and never download or install software."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import verify_ocr_setup


class SetupVerificationTests(unittest.TestCase):
    def test_swedish_letters_and_numbers_are_verified(self):
        result = subprocess.CompletedProcess([], 0, "Svensk text: å ä ö 12345\n".encode(), b"")
        with patch.object(verify_ocr_setup.subprocess, "run", return_value=result) as run:
            verify_ocr_setup.verify("synthetic-tesseract", "synthetic-tessdata")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-l") + 1], "swe")
        self.assertTrue(run.call_args.kwargs["input"].startswith(b"\x89PNG"))

    def test_bad_language_data_or_unreadable_output_cannot_report_success(self):
        for code, output in ((1, "Svensk text: å ä ö 12345"), (0, ""), (0, "Svensk text a a o 12345")):
            with self.subTest(code=code, output=output), patch.object(
                verify_ocr_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output.encode(), b"")
            ), self.assertRaisesRegex(RuntimeError, "Swedish setup test"):
                verify_ocr_setup.verify("synthetic-tesseract", "synthetic-tessdata")
