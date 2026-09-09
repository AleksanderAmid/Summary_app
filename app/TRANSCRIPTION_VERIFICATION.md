# Transcription verification

Local checks on 8 September 2026 used synthetic Swedish text only. These results
are development checks, not a clinical accuracy validation or a representative
performance benchmark.

## Measured scan tests

The test page contained nine lines with a synthetic patient name, date, metformin
500 mg, Hb 136 g/L, potassium 4,2 mmol/L, blood pressure 128/76 mmHg, pulse,
negation and follow-up. The image was 2480 by 3508 pixels. The same page was also
rotated 90 degrees and skewed 3 degrees.

| Reader or case | Measured wall time | Observed result |
|---|---:|---|
| Previous app vision retry loop, Gemma 4 12B, clean page | 182.04 s | Five model requests; complete transcription |
| New Balanced reader, clean page | 1.33 s | One OCR pass; all expected text recovered |
| New Balanced reader, skewed page | 1.12 s | All expected text recovered after preparation |
| New Balanced reader, rotated page | 0.91 s | All expected text recovered after rotation |
| New Thorough reader, clean page, first Paddle use | 40.55 s | Both OCR readers used; all expected text recovered |

Whitespace aside, the Balanced outputs matched the synthetic source text. None of
the expected numbers, units, dates or negation tokens were missing in these three
cases. The previous-reader timing includes its five short-output retries, so this
large difference must not be extrapolated to all pages. Paddle startup and warm
inference differ substantially. The workstation was also running development
checks; these are individual observations, not repeated timing distributions.

A separate scan table covered Hb, potassium, sodium, CRP <5 mg/L and TSH. Testing
found three-column ordering errors, including slightly misaligned cell boxes.
Both cases were fixed and given regression tests. A final real two-reader pass
kept all six header/lab rows in label-result-unit order. Paddle read `Återbesök`
as `Aterbesök`; this disagreement was exposed as a review warning, rather than
silently corrected.
A mixed PDF with a native header and scanned body was correctly routed through
OCR and retained text from both areas.

## Regression coverage

All 52 tests across the transcription, batch/update, and privacy/export suites
passed. A real two-scan run through the browser completed OCR, pseudonymisation,
summary generation and identifier restoration on synthetic patient text.

`test_transcription.py` covers short native pages, native headers over scans,
duplicate-page reuse and order, blank pages, page failures, Word tables and images,
prose columns and lab rows, dark backgrounds, Unicode input, JSON validation,
reading-mode validation, low-confidence routing, disagreement warnings, retention
of warnings in downloads, and bounded retries on truncated or repetitive vision
output. Existing batch, encryption, export and Git update tests remain applicable.

## Limits

The examples do not establish accuracy on handwriting, unusual forms, degraded
clinical scans or every identifier category. OCR confidence and agreement are
routing and review signals. No generative spelling correction, numeric correction,
clinical inference, or automatic replacement of uncertain digits is applied after
OCR. Final summaries still need comparison with their sources.
## Five-page concurrency check, 9 September 2026

The app now uses five OCR slots and five identifier-detection slots. Nine new
concurrency tests passed, bringing the four app regression suites to 61 passing
tests. The tests force simultaneous work and out-of-order completion, and verify
source order, shared concurrency limits, independent Paddle predictors, failure
handling, global pseudonym consistency, and waiting for all results before
summary generation. The historical research-corpus test remains separate.

Five copies of the same synthetic clean scan were processed concurrently:

| Check | Wall time | Observed result |
|---|---:|---|
| Balanced, five pages | 2.28 s | All five used Tesseract and returned identical text with the expected dose |
| Thorough, five pages, cold Paddle workers | 84.41 s | Five distinct worker processes; matching text and no reader disagreements |

Ollama was restarted with `OLLAMA_NUM_PARALLEL=5`; its runtime reported
`n_seq_max = 5`. These are individual local development measurements. Cold model
loading, available memory, document complexity and competing work affect timing.

A complete browser run with five synthetic scanned documents then finished:
OCR 2.0 s, identifier detection 56.2 s (including the initial model load), summary
52.1 s, and identifier restoration under 0.1 s. All five identifier requests
occupied separate Ollama slots before the first completed. No identifier checks
fell back to rules. Document order was preserved, the same patient name had one
consistent placeholder across five pages, the summary contained no unresolved
name/date placeholders, and no encrypted mapping file remained after completion.
