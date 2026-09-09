"""Synthetic coverage and real runtime-contract regressions for long summaries."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import ollama_client
import summary_context as context
import summarization as summary

CAPABILITIES = {"ollama_version": "0.32.1", "model_context": 262144}
OVERFLOW = '{"error":{"code":400,"message":"request (6316 tokens) exceeds the available context size (2048 tokens)","type":"exceed_context_size_error"}}'


def complete(evidence="E0001"):
    return {"done": True, "done_reason": "stop", "prompt_eval_count": 250,
            "response": json.dumps({"sentences": [{"text": "Kontroll planeras.",
                                                    "evidence": [evidence]}], "uncertainties": []})}


class ContextSizingTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "131072"})
        environment.start()
        self.addCleanup(environment.stop)
        context._runtime_capabilities.cache_clear()

    def test_long_input_selects_larger_context_and_short_input_stays_small(self):
        short = context.context_plan("system", "text", "schema", 2048, CAPABILITIES)
        long = context.context_plan("system", "å" * 95140, "schema", 2048, CAPABILITIES)
        self.assertEqual(short["contexts"][0], 8192)
        self.assertGreater(long["contexts"][0], 32768)
        self.assertEqual(long["contexts"][-1], 131072)

    def test_native_and_configured_limits_are_both_respected(self):
        with patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "262144"}):
            plan = context.context_plan("", "x" * 900000, "", 2048, CAPABILITIES)
            self.assertEqual(plan["contexts"], [262144])
        plan = context.context_plan("", "x" * 900000, "", 2048,
                                    dict(CAPABILITIES, model_context=32768))
        self.assertEqual(plan["contexts"], [32768])
        with patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "60000"}):
            plan = context.context_plan("", "x" * 200000, "", 2048, CAPABILITIES)
            self.assertEqual(plan["contexts"], [60000])

    def test_invalid_limits_fail_clearly(self):
        for value in ("0", "abc", "262145"):
            with self.subTest(value=value), patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": value}):
                with self.assertRaisesRegex(RuntimeError, "SMARTDOC_SUMMARY_MAX_CONTEXT"):
                    context.configured_limit()

    def test_supported_runtime_reads_native_capacity(self):
        with patch.object(ollama_client, "get_json", return_value={"version": "0.32.1"}), \
                patch.object(ollama_client, "post_json", return_value={"model_info": {
                    "general.architecture": "gemma4", "gemma4.context_length": 262144}}):
            self.assertEqual(context.runtime_capabilities("test"), CAPABILITIES)

    def test_older_runtime_and_unknown_model_capacity_fail_closed(self):
        with patch.object(ollama_client, "get_json", return_value={"version": "0.31.0"}):
            with self.assertRaisesRegex(RuntimeError, "no-truncation"):
                context.runtime_capabilities("old")
        with patch.object(ollama_client, "get_json", return_value={"version": "0.32.1"}), \
                patch.object(ollama_client, "post_json", return_value={"model_info": {}}):
            with self.assertRaisesRegex(RuntimeError, "usable context"):
                context.runtime_capabilities("unknown")

    def test_http_nested_overflow_is_recognised_but_memory_failure_is_not(self):
        error = urllib.error.HTTPError("http://localhost", 400, "Bad Request", {},
                                      BytesIO(json.dumps({"error": OVERFLOW}).encode()))
        with patch.object(ollama_client.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ollama_client.OllamaError) as raised:
                ollama_client.post_json("/api/generate", {})
        self.assertTrue(raised.exception.context_overflow)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(ollama_client.OllamaError(500, "out of memory").context_overflow)


class FullSourceTests(unittest.TestCase):
    def setUp(self):
        for mock in (patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "131072"}),
                     patch.object(context, "runtime_capabilities", return_value=CAPABILITIES)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_previous_size_failure_now_preserves_every_unit_and_last_citation(self):
        source = "\n\n".join(f"Avsnitt {n}: " + "Journalanteckning för granskning. " * 35 for n in range(100))
        units = summary.evidence_units(source)
        with patch.object(ollama_client, "post_json", return_value=complete(units[-1]["id"])) as generate:
            result = summary.summarize(source)
        request = generate.call_args.args[1]
        self.assertGreater(request["options"]["num_ctx"], 32768)
        self.assertLessEqual(request["options"]["num_ctx"], 131072)
        self.assertFalse(request["truncate"])
        self.assertFalse(request["shift"])
        self.assertEqual(request["prompt"], summary.build_user_prompt(source))
        self.assertEqual(result["evidence"], units)
        self.assertIn(units[-1]["id"], result["summary"])

    def test_actual_tokenizer_overflow_retries_larger_without_losing_source(self):
        with patch.object(ollama_client, "post_json", side_effect=[
                ollama_client.OllamaError(400, OVERFLOW), complete()]) as generate:
            result = summary.summarize("Kontroll planeras.")
        requests = [c.args[1] for c in generate.call_args_list]
        self.assertEqual([r["options"]["num_ctx"] for r in requests], [8192, 16384])
        self.assertEqual(requests[0]["prompt"], requests[1]["prompt"])
        self.assertEqual(result["telemetry"]["context_retries"], 1)

    def test_estimate_above_cap_still_lets_actual_tokenizer_decide(self):
        with patch.object(context, "estimate_tokens", return_value=900000), \
                patch.object(ollama_client, "post_json", return_value=complete()) as generate:
            result = summary.summarize("Kontroll planeras.")
        self.assertEqual(generate.call_args.args[1]["options"]["num_ctx"], 131072)
        self.assertTrue(result["telemetry"]["full_source_in_prompt"])

    def test_exhausted_capacity_raises_instead_of_returning_partial_output(self):
        with patch.dict(os.environ, {"SMARTDOC_SUMMARY_MAX_CONTEXT": "16384"}), \
                patch.object(ollama_client, "post_json", side_effect=ollama_client.OllamaError(400, OVERFLOW)) as generate:
            with self.assertRaisesRegex(RuntimeError, "16,384-token.*No source text was trimmed"):
                summary.summarize("Kontroll planeras.")
        self.assertEqual(generate.call_count, 2)

    def test_unrelated_runtime_failure_does_not_trigger_larger_allocations(self):
        with patch.object(ollama_client, "post_json", side_effect=ollama_client.OllamaError(500, "out of memory")) as generate:
            with self.assertRaisesRegex(RuntimeError, "out of memory"):
                summary.summarize("Kontroll planeras.")
        generate.assert_called_once()

    def test_format_failure_only_retries_once_at_same_context(self):
        with patch.object(ollama_client, "post_json", side_effect=[{"response": "not JSON"}, complete()]) as generate:
            result = summary.summarize("Kontroll planeras.")
        requests = [c.args[1] for c in generate.call_args_list]
        self.assertEqual(requests[0]["options"]["num_ctx"], requests[1]["options"]["num_ctx"])
        self.assertTrue(requests[1]["prompt"].startswith(requests[0]["prompt"]))
        self.assertEqual(result["telemetry"]["attempts"], 2)

    def test_empty_source_does_not_call_the_runtime(self):
        with patch.object(ollama_client, "post_json") as generate:
            with self.assertRaisesRegex(RuntimeError, "No source text"):
                summary.summarize(" ")
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
