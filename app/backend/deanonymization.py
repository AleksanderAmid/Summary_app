"""De-anonymization stage for the Medical Summary app.

The thesis de-identification pipeline (src/deidentification/) replaced real
PHI with consistent fake values and stored a per-patient code book
(structure: { "<real value>": {"fake": "<fake value>", "type": "<PHI type>"} }).
The app ships with bundled copies at app/study_data/codebooks/<patient_id>.json
(copied from data/deidentified/<patient_id>/_mapping.json) so the folder is
self-contained and movable to another device.

De-anonymization inverts that code book: every fake value found in the
generated summary is replaced by the corresponding real value, so the final
output refers to the actual patient again.

Because the app cannot know which patient an uploaded document belongs to,
it auto-detects the code book: each patient's distinctive fake values are
counted in the *source text* (which is what the model saw), and the mapping
with the most hits wins. If no mapping matches, the summary is returned
unchanged — correct behaviour for documents that were never de-identified
by the study pipeline.

Irreversible deletions (fake values like "[telefon borttagen]") are skipped:
several distinct real values share one deletion placeholder, so no unique
inverse exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
CODEBOOK_DIR = APP_ROOT / "study_data" / "codebooks"

# Patient detection relies on DISTINCTIVE fake values only. The study's
# Faker-based pipeline reused generic surnames/cities ("Larsson",
# "Stockholm", "Örebro") across patients, so common single tokens are noise;
# personnummer and multi-token names/addresses are near-unique per patient.
DETECT_TYPES = {"PER", "PERSONNUMMER", "ORG", "ADDRESS"}
PERSONNUMMER_WEIGHT = 3
MIN_DETECTION_SCORE = 2


def _load_mapping(path: Path) -> list[dict]:
    """Load one _mapping.json into a clean list of {real, fake, type} pairs."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    pairs = []
    for real, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        fake = (entry.get("fake") or "").strip()
        kind = (entry.get("type") or "").strip()
        real = (real or "").strip()
        if not real or not fake or fake == real:
            continue
        if fake.startswith("["):          # deletion placeholder — irreversible
            continue
        if "\n" in real or "\n" in fake:  # OCR-noise keys — unusable
            continue
        pairs.append({"real": real, "fake": fake, "type": kind})
    return _drop_ambiguous(pairs)


def _drop_ambiguous(pairs: list[dict]) -> list[dict]:
    """Resolve fakes that map back to more than one real value.

    Two cases occur in the study's code books:

    * NER glue — the same fake was assigned to OCR-glued variants of one
      entity ('Hönö Vårdcentral', 'Läkare Adam Goldberg Hönö Vårdcentral',
      ... all -> fake 'Havets Vårdcentral'). The shortest real is contained
      in every variant, so it IS the deterministic inverse: keep one pair
      restoring to that common core.
    * True conflicts — one fake for different entities ('Helena' ->
      BIRGITTA and 'Helena' -> Lina). No deterministic inverse exists, so
      restoring would corrupt the summary: drop the whole group.
    """
    groups: dict[str, list[dict]] = {}
    for p in pairs:
        groups.setdefault(p["fake"], []).append(p)
    resolved = []
    for group in groups.values():
        uniq = {p["real"].lower(): p for p in group}
        if len(uniq) == 1:
            resolved.append(group[0])
            continue
        core = min(uniq.values(), key=lambda p: len(p["real"]))
        if all(core["real"].lower() in r for r in uniq):
            resolved.append(core)
    return resolved


def _is_distinctive(pair: dict) -> bool:
    """True for fake values that identify a patient with high confidence."""
    if pair["type"] == "PERSONNUMMER":
        return True
    if pair["type"] in DETECT_TYPES:
        return len(pair["fake"].split()) >= 2 and len(pair["fake"]) >= 8
    return False


def load_all_mappings() -> dict[str, list[dict]]:
    """patient_id -> cleaned mapping pairs, for every study patient."""
    mappings: dict[str, list[dict]] = {}
    if not CODEBOOK_DIR.is_dir():
        return mappings
    for mapping_file in sorted(CODEBOOK_DIR.glob("*.json")):
        pairs = _load_mapping(mapping_file)
        if pairs:
            mappings[mapping_file.stem] = pairs
    return mappings


# Swedish clinical-title tokens that NER sometimes glued onto names in the
# code books (real 'Dr Goldberg' -> fake 'Lotta Wallin'); deriving
# 'Lotta' -> 'Dr' from such entries would put bare titles where names belong.
TITLE_TOKENS = {"dr", "st", "läk", "md", "leg", "ssk", "usk",
                "doktor", "läkare", "överläkare", "dl", "vc"}


def _person_token_pairs(pairs: list[dict]) -> list[dict]:
    """Derive partial-name pairs so 'Andersson' or 'Josefin Andersson' map back.

    The code book stores full names ('Josefin Christina Lovisa Andersson');
    summaries often use first + last name only. For each PER pair, map first
    token -> first token and last token -> last token, provided the fake
    token is unambiguous within the patient's mapping AND the real token
    actually looks like a name: several code-book PER entries are OCR-glued
    strings like real 'Gustav Joelsson Hönö Vårdcentral' whose edge tokens
    are organisations, places, or titles — deriving those would corrupt
    summaries (e.g. 'dr Norberg' -> 'dr Vårdcentral').
    """
    non_person_tokens = {t.lower() for p in pairs
                         if p["type"] in ("ORG", "LOC", "ADDRESS")
                         for t in p["real"].split()}
    derived = []
    fake_tokens_seen: dict[str, int] = {}
    candidates = []
    for p in pairs:
        if p["type"] != "PER":
            continue
        f_toks, r_toks = p["fake"].split(), p["real"].split()
        if len(f_toks) < 2 or len(r_toks) < 2:
            continue
        for f_tok, r_tok in ((f_toks[0], r_toks[0]), (f_toks[-1], r_toks[-1])):
            if (len(f_tok) >= 3 and len(r_tok) >= 2
                    and r_tok.lower() not in non_person_tokens
                    and r_tok.lower() not in TITLE_TOKENS):
                fake_tokens_seen[f_tok] = fake_tokens_seen.get(f_tok, 0) + 1
                candidates.append({"real": r_tok, "fake": f_tok, "type": "PER_PARTIAL"})
    for c in candidates:
        if fake_tokens_seen[c["fake"]] == 1:   # unambiguous within this patient
            derived.append(c)
    return derived


def detect_patient(source_text: str,
                   mappings: dict[str, list[dict]] | None = None) -> tuple[str | None, int]:
    """Return (patient_id, score) for the best-matching code book.

    Scores only distinctive fakes (see _is_distinctive); personnummer hits
    are weighted extra. Below MIN_DETECTION_SCORE no patient is reported —
    a pass-through beats restoring the wrong patient's identifiers.
    """
    if mappings is None:
        mappings = load_all_mappings()
    best_pid, best_score = None, 0
    for pid, pairs in mappings.items():
        score = 0
        for p in pairs:
            if not _is_distinctive(p):
                continue
            n = source_text.count(p["fake"])
            if n:
                weight = PERSONNUMMER_WEIGHT if p["type"] == "PERSONNUMMER" else 1
                score += n * weight
        if score > best_score:
            best_pid, best_score = pid, score
    if best_score < MIN_DETECTION_SCORE:
        return None, 0
    return best_pid, best_score


def deanonymize(summary: str, source_text: str) -> dict:
    """Replace fake identifiers in the summary with the real values.

    Returns {text, patient_id, detection_hits, replacements:[{fake, real,
    type, count}]}. Pass-through when no code book matches the source.
    """
    mappings = load_all_mappings()
    pid, hits = detect_patient(source_text, mappings)
    if pid is None or hits == 0:
        return {"text": summary, "patient_id": None, "detection_hits": 0,
                "replacements": []}

    pairs = mappings[pid] + _person_token_pairs(mappings[pid])
    # Longest fake first so full names are restored before partial tokens.
    pairs.sort(key=lambda p: len(p["fake"]), reverse=True)

    # Single-pass replacement via one alternation regex. Sequential per-pair
    # substitution would rescan already-restored text and corrupt chained
    # values (the code books contain chains like fake 'Stockholm' -> real
    # 'Mölndal' while fake 'Mölndal' -> real 'Hönö'); with one pass an
    # inserted real value is never matched again.
    lookup: dict[str, dict] = {}
    for p in pairs:
        lookup.setdefault(p["fake"], p)  # longest-first order: first wins
    fakes = list(lookup)
    if not fakes:
        return {"text": summary, "patient_id": pid, "detection_hits": hits,
                "replacements": []}
    # Word-boundary match so 'Mölndal' does not fire inside another word.
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(f) for f in fakes) + r")(?!\w)")
    counts: dict[str, int] = {}

    def _restore(match: re.Match) -> str:
        fake = match.group(0)
        counts[fake] = counts.get(fake, 0) + 1
        return lookup[fake]["real"]

    text = pattern.sub(_restore, summary)
    replacements = [{"fake": f, "real": lookup[f]["real"],
                     "type": lookup[f]["type"], "count": n}
                    for f, n in counts.items()]
    return {"text": text, "patient_id": pid, "detection_hits": hits,
            "replacements": replacements}
