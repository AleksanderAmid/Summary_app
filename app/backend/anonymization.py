"""Anonymization stage — placeholder for future implementation.

The full three-layer de-identification pipeline of the thesis (§3.4:
regex recognizers + KB-BERT NER + consistent Faker replacement, see
src/deidentification/) is intentionally NOT wired into the interactive app
yet; the specification marks this stage as future work.

What this module does today is a conservative regex-only PHI screen
(patterns aligned with src/deidentification/recognizers.py) so the app can
warn when the input still appears to contain direct identifiers.
"""
from __future__ import annotations

import re

_PHI_PATTERNS = [
    ("personnummer", re.compile(
        r"\b(?:19|20)?\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?\d{4}\b")),
    ("phone", re.compile(r"\b(?:\+46|0)\d{1,3}[- ]?\d{2,3}[- ]?\d{2,3}[- ]?\d{2,3}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("postal_code", re.compile(r"\b\d{3} ?\d{2}\b")),
]


def phi_screen(text: str) -> dict[str, int]:
    """Count likely direct identifiers per category. Advisory only."""
    hits: dict[str, int] = {}
    for label, pattern in _PHI_PATTERNS:
        n = len(pattern.findall(text))
        if n:
            hits[label] = n
    return hits


def run_anonymization_stage(text: str) -> dict:
    """Future implementation hook.

    Returns the text unchanged plus screening metadata. When the §3.4
    pipeline is integrated, this is the single place to plug it in: it
    should return the anonymized text together with the real->fake mapping
    that the de-anonymization stage then inverts.
    """
    return {
        "text": text,
        "implemented": False,
        "phi_hits": phi_screen(text),
        "mapping": None,
    }
