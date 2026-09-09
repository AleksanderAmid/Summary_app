"""Model-aware context sizing with explicit runtime protection against truncation."""
from functools import lru_cache
import math
import os
import re
import time

import ollama_client

DEFAULT_MAX_CONTEXT = 131072
CONTEXT_SIZES = (8192, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144)
MIN_OLLAMA = (0, 32, 1)


@lru_cache(maxsize=8)
def _runtime_capabilities(model, minute):
    version = ollama_client.get_json("/api/version").get("version", "")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_OLLAMA:
        raise RuntimeError("Update Ollama to version 0.32.1 or newer before generating summaries. "
                           "This app requires its explicit no-truncation controls.")
    details = ollama_client.post_json("/api/show", {"model": model}, timeout=15)
    info = details.get("model_info", {})
    architecture = info.get("general.architecture", "")
    native = info.get(architecture + ".context_length")
    if not isinstance(native, int) or isinstance(native, bool) or native < 4096:
        raise RuntimeError("The local model did not report a usable context capacity. "
                           "Check the selected model in Ollama before retrying.")
    return {"ollama_version": version, "model_context": native}


def runtime_capabilities(model):
    return dict(_runtime_capabilities(model, int(time.monotonic()//60)))


def configured_limit():
    raw = os.environ.get("SMARTDOC_SUMMARY_MAX_CONTEXT", str(DEFAULT_MAX_CONTEXT))
    try:
        limit = int(raw)
    except ValueError:
        raise RuntimeError("SMARTDOC_SUMMARY_MAX_CONTEXT must be a whole token count.") from None
    if not 4096 <= limit <= 262144:
        raise RuntimeError("SMARTDOC_SUMMARY_MAX_CONTEXT must be between 4096 and 262144.")
    return limit


def estimate_tokens(system, prompt, schema, output_tokens):
    # A sizing heuristic, not tokenization. The runtime rejects rather than trims
    # underestimated inputs. UTF-8 bytes account for Swedish and non-Latin text.
    prompt_bytes = len((system + prompt + schema).encode("utf-8"))
    return math.ceil(prompt_bytes/3) + output_tokens + 768


def context_plan(system, prompt, schema, output_tokens, capabilities):
    maximum = min(configured_limit(), capabilities["model_context"])
    sizes = sorted({size for size in CONTEXT_SIZES if size <= maximum} | {maximum})
    estimate = estimate_tokens(system, prompt, schema, output_tokens)
    selected = next((size for size in sizes if size >= estimate), maximum)
    # If the heuristic overshoots, let the actual model tokenizer decide at the
    # cap. No rejection or source exclusion is based on character count alone.
    return {"contexts": [size for size in sizes if size >= selected],
            "estimated_tokens": estimate, "maximum_context": maximum,
            "model_context": capabilities["model_context"],
            "ollama_version": capabilities["ollama_version"]}
