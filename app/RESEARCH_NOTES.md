# Changes informed by the supplied thesis

Reviewed source: *AI Driven Summarization of Swedish Electronic Health Record
Patient Journals*, supplied PDF version `(10)`, 97 PDF pages. Page numbers below
refer to PDF pages rather than the printed chapter pagination.

## Findings applied

- **Keep evaluation data out of generation.** The physician reference summaries
  are evaluation-only in the supplied method (PDF pp. 44–48). The app previously
  placed all 25 references in the system prompt. This dependency has been removed.
- **Use consistent typed pseudonyms and validated detector spans.** The thesis
  combines structured rules with local model suggestions, accepting exact source
  spans and recording fallback behaviour (pp. 44–48, 62–63). The app now follows
  this pattern and exposes fallback and residual-identifier warnings. The mapping
  is encrypted temporarily and used to restore the final summary, as requested.
- **Make evidence and generation validity inspectable.** The thesis stresses
  source-linked output, preservation of clinical polarity and time status, and
  incomplete-output handling (pp. 44–48, 66–71). The app now requires JSON with
  source IDs for every sentence, checks references, limits summary length, flags
  numeric discrepancies and rejects incomplete responses. Clinical entailment
  and omission are not automatically verified by these checks.
- **Separate model configuration from winner claims.** The supplied study covers
  Gemma 4 12B, Qwen3.5-9B and the AI Sweden Llama model; its results do not establish
  a universal clinical winner (pp. 57–64, 69–71). The app uses the locally installed
  Gemma 4 12B by default and displays configuration rather than obsolete scores.
- **Treat context coverage as a requirement.** Serving limits and incomplete
  output can distort results. The app retains all input passages in its direct
  prompt and rejects inputs estimated to exceed the supported budget. This does
  not implement or reproduce the thesis retrieval experiments.

## Further improvements requiring evaluation

The thesis detector did not meet its recall target in all categories. This
implementation is a new detector configuration, not a reproduction of a measured
recall result. It needs a held-out Swedish dataset with span-level checks for
names, identity numbers, dates, ages, addresses and organisations, including OCR
errors and case variants. Human review remains necessary before sharing outputs.

The OCR comparison favours dedicated OCR approaches in the tested setting. The
app now combines Swedish Tesseract with local PaddleOCR, uses 300-DPI scan
preparation, checks native-text/image coverage and reports reader disagreements.
Balanced routing and layout reconstruction are app engineering choices, not
reproductions of the thesis benchmark. Thorough mode cross-checks every scanned
page. Runtime and accuracy still need evaluation on representative held-out scans,
especially handwriting, dense forms and lab tables. Pseudonymised exports contain
extracted text; this does not redact or modify the original page images.

Evaluate summary omissions, unsupported claims, conflicting dates, medication
status, units and negations using held-out cases and clinician review. Keep these
cases and physician references out of the generation prompt. Source IDs establish
traceability, not a validated medical quality score. Test long-document context
coverage against the chosen model's actual tokenizer before raising input limits.

Implementation reference for structured model responses:
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

OCR implementation references:
- [Tesseract image preparation](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
- [PaddleOCR local models and pipeline options](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
