"""Synthetic privacy, summary-contract, and export regression tests."""
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import anonymization as anon
import exports
import privacy
import pipeline
import summarization as summary

SOURCE = ("Patient: Erik Exempelsson\nHan är man.\nPersonnummer: 19900101-0017\n"
          "Datum: 2026-08-01\nE-post: erik@example.invalid\nTelefon: 070-123 45 67\n"
          "Erik Exempelsson tar metformin 500 mg. Hb 136 g/L. Ingen penicillinallergi.")
SPAN = (SOURCE.index("Erik Exempelsson"), SOURCE.index("Erik Exempelsson")+len("Erik Exempelsson"), "PATIENT_MALE")


class PrivacyTests(unittest.TestCase):
    def test_rules_and_hybrid_keep_clinical_values_and_consistent_placeholders(self):
        with patch.object(anon, "llm_spans", return_value=([SPAN], 0)):
            result = anon.run_anonymization_stage(SOURCE)
        self.assertNotIn("Erik", result["text"])
        self.assertNotIn("19900101", result["text"])
        self.assertNotIn("example.invalid", result["text"])
        self.assertNotIn("070-123", result["text"])
        self.assertIn("metformin 500 mg", result["text"])
        self.assertIn("Hb 136 g/L", result["text"])
        self.assertEqual(result["text"].count("[NAME_PATIENT_MALE_01]"), 2)
        self.assertEqual(result["residual_flags"], {})
        self.assertTrue(anon.valid_identity("19900101-0017"))
        self.assertFalse(anon.valid_identity("19900101-0018"))

    def test_verbatim_span_validation_and_failure_reporting(self):
        payload = {"entities": [{"text": "Invented Person", "type": "PERSON"},
                                {"text": "Erik Exempelsson", "type": "PATIENT_MALE"},
                                {"text": "metformin", "type": "DIAGNOSIS"}]}
        with patch.object(anon.ollama_client, "post_json", return_value={"response": json.dumps(payload)}):
            spans, discarded = anon.llm_spans(SOURCE, "test")
        self.assertEqual(discarded, 2)
        self.assertEqual(len(spans), 2)
        with patch.object(anon, "llm_spans", side_effect=RuntimeError("offline")):
            result = anon.run_anonymization_stage(SOURCE, additional=["metformin"])
        self.assertEqual(result["fallback_passages"], 1)
        self.assertNotIn("Erik Exempelsson", result["text"])
        self.assertNotIn("metformin", result["text"])
        self.assertTrue(any("rules" in warning for warning in result["warnings"]))

    def test_mapping_ciphertext_restore_and_cleanup_even_on_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                with privacy.MappingVault("test", folder) as vault:
                    vault.seal({"Erik Exempelsson": {"token": "[NAME_PATIENT_MALE_01]", "type": "PATIENT_MALE"}})
                    self.assertNotIn(b"Erik", vault.path.read_bytes())
                    self.assertEqual(vault.restore("Hej [NAME_PATIENT_MALE_01]."), "Hej Erik Exempelsson.")
                    raise RuntimeError("test")
            self.assertFalse(list(Path(folder).iterdir()))

    def test_pipeline_restores_summary_but_not_pseudonymised_source(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(privacy, "PRIVATE_DIR", Path(folder)), \
                patch.object(anon, "llm_spans", return_value=([SPAN], 0)), \
                patch.object(pipeline, "_jobs", {"test": {"stages": pipeline._new_stages()}}), \
                patch.object(pipeline.history, "save_record") as saved, \
                patch.object(summary, "summarize") as generate:
            generate.return_value = {"summary": "[NAME_PATIENT_MALE_01] tar metformin 500 mg. [E0001]",
                                     "telemetry": {"eval_count": 10}, "evidence": []}
            record = pipeline._execute("test", {"text": SOURCE})
            self.assertIn("Erik Exempelsson", record["summary"])
            self.assertNotIn("Erik", record["pseudonymised_text"])
            self.assertNotIn("Erik", generate.call_args.args[0])
            self.assertNotIn("mapping", record["pseudonymisation"])
            self.assertFalse(list(Path(folder).glob("*.enc")))
            self.assertFalse(record["privacy"]["mapping_retained"])
            saved.assert_called_once()

    def test_generation_failure_removes_encrypted_mapping(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(privacy, "PRIVATE_DIR", Path(folder)), \
                patch.object(anon, "llm_spans", return_value=([SPAN], 0)), \
                patch.object(pipeline, "_jobs", {"test": {"stages": pipeline._new_stages()}}), \
                patch.object(summary, "summarize", side_effect=RuntimeError("generation failed")):
            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                pipeline._execute("test", {"text": SOURCE})
            self.assertFalse(list(Path(folder).glob("*.enc")))


class SummaryTests(unittest.TestCase):
    def setUp(self):
        capabilities = patch.object(summary.summary_context, "runtime_capabilities",
                                    return_value={"ollama_version": "0.32.1", "model_context": 262144})
        capabilities.start()
        self.addCleanup(capabilities.stop)

    def test_generation_uses_no_physician_references_and_cites_source(self):
        payload = {"sentences": [{"text": "Ingen penicillinallergi.", "evidence": ["E0001"]}], "uncertainties": []}
        with patch.object(summary.ollama_client, "post_json",
                          return_value={"response": json.dumps(payload), "done": True, "done_reason": "stop",
                                        "eval_count": 20, "prompt_eval_count": 150}) as call:
            result = summary.summarize("Ingen penicillinallergi.")
        self.assertIn("[E0001]", result["summary"])
        sent = call.call_args.args[1]
        self.assertNotIn("REFERENSSAMMANFATTNINGAR", sent["system"])
        self.assertTrue(result["telemetry"]["full_source_in_prompt"])

    def test_invalid_citations_and_truncated_json_are_not_saved_as_complete(self):
        invalid = {"sentences": [{"text": "Test.", "evidence": ["E9999"]}], "uncertainties": []}
        with patch.object(summary.ollama_client, "post_json", return_value={"response": json.dumps(invalid)}):
            with self.assertRaisesRegex(RuntimeError, "source references"):
                summary.summarize("Test.")
        with patch.object(summary.ollama_client, "post_json",
                          return_value={"response": "{}", "done_reason": "length"}):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                summary.summarize("Test.")

    def test_all_source_units_preserved_and_oversized_inputs_fail_explicitly(self):
        text = "Section one.\n\n" + "Section two. " * 250
        units = summary.evidence_units(text)
        self.assertEqual(" ".join(" ".join(u["text"].split()) for u in units), " ".join(text.split()))
        error = summary.ollama_client.OllamaError(400, "request exceeds the available context size")
        with patch.object(summary.ollama_client, "post_json", side_effect=error) as call:
            with self.assertRaisesRegex(RuntimeError, "No source text was trimmed"):
                summary.summarize("too much text " * 15000)
            self.assertTrue(call.called)
            self.assertTrue(all(not c.args[1]["truncate"] for c in call.call_args_list))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.record = {"summary": "Erik Exempelsson: Hb 136 g/L och åäö. [E0001]",
                       "pseudonymised_text": "[NAME_PATIENT_MALE_01]: Hb 136 g/L och åäö.",
                       "pseudonymisation": {"implemented": True}, "evidence": []}

    def test_all_formats_and_both_selection(self):
        for format in exports.FORMATS:
            with self.subTest(format=format):
                name, mime, content = exports.export_record(self.record, "both", format)
                self.assertEqual(name, "smartdoc-documents.zip")
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    self.assertEqual(set(archive.namelist()),
                        {"summary." + format, "pseudonymised-documents." + format})
                    pseudo = archive.read("pseudonymised-documents." + format)
                if format in ("txt", "md"):
                    self.assertIn("åäö", pseudo.decode())
                    self.assertNotIn(b"Erik", pseudo)
                elif format == "docx":
                    with zipfile.ZipFile(BytesIO(pseudo)) as document:
                        self.assertNotIn(b"Erik", document.read("word/document.xml"))
                        self.assertIn(b"NAME_PATIENT_MALE", document.read("word/document.xml"))
                        self.assertNotIn(b"Erik", document.read("docProps/core.xml"))
                else:
                    from pypdf import PdfReader
                    pdf = PdfReader(BytesIO(pseudo))
                    text = "".join(page.extract_text() for page in pdf.pages)
                    self.assertIn("åäö", text)
                    self.assertNotIn("Erik", text)

    def test_pdf_preview_renders_real_pages_and_rejects_missing_pages(self):
        import fitz
        image, count = exports.preview_pdf(self.record, "pseudonymised")
        self.assertEqual(count, 1)
        with fitz.open(stream=image, filetype="png") as rendered:
            self.assertGreater(rendered[0].rect.width, 600)
        with self.assertRaises(ValueError):
            exports.preview_pdf(self.record, "summary", 0)
        with self.assertRaises(ValueError):
            exports.preview_pdf(self.record, "summary", 2)

    def test_legacy_pseudonymised_export_rejected_and_file_types_validated(self):
        with self.assertRaisesRegex(ValueError, "older run"):
            exports.export_record({"summary": "Old summary"}, "pseudonymised", "txt")
        with self.assertRaises(ValueError):
            exports.export_record(self.record, "summary", "exe")


if __name__ == "__main__":
    unittest.main()
