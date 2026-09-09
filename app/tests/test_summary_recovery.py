"""Generation recovery must preserve the whole source and encrypted mapping."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import anonymization
import pipeline
import privacy
import summarization as summary

CAPABILITIES = {"ollama_version": "0.33.3", "model_context": 262144}


def completed(text="Kontroll planeras.", evidence=None):
    return {"done": True, "done_reason": "stop", "prompt_eval_count": 250, "eval_count": 50,
            "response": json.dumps({"sentences": [{"text": text, "evidence": evidence or ["E0001"]}],
                                    "uncertainties": []})}


class OutputRecoveryTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "131072"}),
                     patch.object(summary.summary_context, "runtime_capabilities", return_value=CAPABILITIES)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_output_limit_recovery_keeps_every_source_section_and_reserves_room(self):
        source = "\n\n".join(f"Anteckning {i}. Kontroll planeras." for i in range(20))
        # Even syntactically valid output must be rejected if the model reports
        # truncation. Its measured input count requires more context on retry.
        incomplete = dict(completed("Avbruten mening."), done_reason="length", prompt_eval_count=7000,
                          eval_count=4096)
        with patch.object(summary.ollama_client, "post_json", side_effect=[
                incomplete, completed(evidence=["E0020"])]) as generate:
            updates = []
            result = summary.summarize(source, updates.append)
        requests = [call.args[1] for call in generate.call_args_list]
        self.assertEqual([r["options"]["num_predict"] for r in requests], [4096, 8192])
        self.assertEqual([r["options"]["num_ctx"] for r in requests], [8192, 16384])
        for request in requests:
            self.assertTrue(request["prompt"].startswith(summary.build_user_prompt(source)))
            self.assertFalse(request["truncate"])
            self.assertFalse(request["shift"])
        self.assertEqual(result["evidence"], summary.evidence_units(source))
        self.assertNotIn("Avbruten", result["summary"])
        self.assertIn("E0020", result["summary"])
        self.assertEqual(result["telemetry"]["output_retries"], 1)
        self.assertEqual(result["telemetry"]["output_token_limit"], 8192)
        self.assertEqual([item["outcome"] for item in result["telemetry"]["attempt_details"]],
                         ["incomplete", "complete"])
        self.assertTrue(any("preparation is already complete" in text for text in updates))
        self.assertNotIn("Anteckning", json.dumps(result["telemetry"]))

    def test_repeated_incomplete_responses_stop_after_bounded_retries(self):
        for flags in ({"done_reason": "length"}, {"done": False}):
            with self.subTest(flags=flags), patch.object(summary.ollama_client, "post_json",
                    return_value=dict(completed(), **flags)) as generate:
                with self.assertRaisesRegex(RuntimeError, "repeatedly stopped.*No partial summary was saved"):
                    summary.summarize("Kontroll planeras.")
                requests = [call.args[1] for call in generate.call_args_list]
                self.assertEqual([r["options"]["num_predict"] for r in requests], [4096, 8192, 16384])
                self.assertTrue(all(r["options"]["num_ctx"] <= 131072 for r in requests))

    def test_output_recovery_and_format_repair_have_separate_bounded_attempts(self):
        with patch.object(summary.ollama_client, "post_json", side_effect=[
                dict(completed(), done_reason="length"), {"response": "invalid JSON"}, completed()]) as generate:
            result = summary.summarize("Kontroll planeras.")
        self.assertEqual(generate.call_count, 3)
        self.assertNotEqual(generate.call_args_list[1].args[1]["prompt"],
                            generate.call_args_list[2].args[1]["prompt"])
        self.assertEqual(result["telemetry"]["format_retries"], 1)
        self.assertEqual(result["telemetry"]["output_retries"], 1)
        self.assertEqual(result["telemetry"]["attempts"], 3)

    def test_context_overflow_after_output_retry_preserves_larger_output_budget(self):
        overflow = summary.ollama_client.OllamaError(400, "request exceeds the available context size")
        with patch.object(summary.ollama_client, "post_json", side_effect=[
                dict(completed(), done_reason="length"), overflow, completed()]) as generate:
            result = summary.summarize("Kontroll planeras.")
        requests = [call.args[1] for call in generate.call_args_list]
        self.assertEqual(requests[1]["prompt"], requests[2]["prompt"])
        self.assertEqual(requests[1]["options"]["num_predict"], requests[2]["options"]["num_predict"])
        self.assertGreater(requests[2]["options"]["num_ctx"], requests[1]["options"]["num_ctx"])
        self.assertEqual(result["telemetry"]["context_retries"], 1)
        self.assertEqual(result["telemetry"]["output_retries"], 1)

    def test_excess_references_and_notes_are_rejected_not_silently_cut(self):
        units = summary.evidence_units("\n\n".join("Kontroll planeras." for _ in range(10)))
        valid = json.loads(completed(evidence=[u["id"] for u in units[:8]])["response"])
        sentences, _, _ = summary.validate_response(json.dumps(valid), units)
        self.assertEqual(len(sentences[0]["evidence"]), 8)
        excessive = json.loads(json.dumps(valid))
        excessive["sentences"][0]["evidence"].append("E0009")
        with self.assertRaisesRegex(ValueError, "source reference limits"):
            summary.validate_response(json.dumps(excessive), units)
        for notes in (["Okänt."] * 9, ["x" * 401], [" "]):
            with self.subTest(notes_count=len(notes)), self.assertRaisesRegex(ValueError, "uncertainty note limits"):
                summary.validate_response(json.dumps(dict(valid, uncertainties=notes)), units)

    def test_pipeline_retry_reuses_preparation_and_cleans_mapping_on_success_and_failure(self):
        source = "Patient: Erik Exempelsson\nHan är man.\nKontroll planeras."
        name = "Erik Exempelsson"
        def detect_name(text, model):
            start = text.index(name)
            return [(start, start + len(name), "PATIENT_MALE")], 0

        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                private = root / "private"
                calls = []

                def model_response(endpoint, request, **kwargs):
                    calls.append(request)
                    self.assertNotIn(name, request["prompt"])
                    encrypted = list(private.glob("*.enc"))
                    self.assertEqual(len(encrypted), 1)
                    self.assertNotIn(name.encode(), encrypted[0].read_bytes())
                    if succeeds and len(calls) == 3:
                        return completed("[NAME_PATIENT_MALE_01] planeras för kontroll.")
                    return dict(completed(), done_reason="length")

                with patch.object(privacy, "PRIVATE_DIR", private), \
                        patch.object(pipeline, "UPLOAD_DIR", root / "uploads"), \
                        patch.object(pipeline, "_jobs", {"test": {"stages": pipeline._new_stages()}}), \
                        patch.object(pipeline.transcription, "transcribe_files", return_value=[
                            (0, {"full_text": source, "method": "test", "pages": []})]) as transcribe, \
                        patch.object(anonymization, "llm_spans", side_effect=detect_name), \
                        patch.object(anonymization, "run_anonymization_stage",
                                     wraps=anonymization.run_anonymization_stage) as anonymize, \
                        patch.object(pipeline.history, "save_record") as saved, \
                        patch.object(summary.ollama_client, "post_json", side_effect=model_response):
                    payload = {"files": [{"filename": "synthetic.txt", "content": source.encode()}]}
                    if succeeds:
                        record = pipeline._execute("test", payload)
                        self.assertIn(name, record["summary"])
                        self.assertNotIn(name, record["pseudonymised_text"])
                        self.assertFalse(record["privacy"]["mapping_retained"])
                        self.assertEqual(record["telemetry"]["output_retries"], 2)
                        saved.assert_called_once()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "repeatedly stopped"):
                            pipeline._execute("test", payload)
                        saved.assert_not_called()
                    transcribe.assert_called_once()
                    anonymize.assert_called_once()
                    self.assertEqual(len(calls), 3)
                    self.assertFalse(list(private.glob("*.enc")))


if __name__ == "__main__":
    unittest.main()
