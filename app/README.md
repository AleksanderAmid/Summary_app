# SmartDoc medical summaries

SmartDoc combines up to 10 documents for one patient, pseudonymises their
extracted text locally, generates a Swedish summary with source references,
and restores the original identifiers in the final summary. Choose Word
(`.docx`), PDF, TXT, or Markdown for the summary, the pseudonymised documents,
or both in a ZIP file.

## Setup and launch

Run `setup.bat` once. It checks Python 3.10+, Ollama 0.32.1 or newer, `gemma4:12b`, and the
packages in `requirements.txt`, followed by Swedish OCR setup (`setup_ocr.ps1`).
On an existing installation, install the new packages before restarting:

```powershell
python -m pip install -r app/requirements.txt
```

Double-click `SmartDoc.vbs` inside `app/` to open http://localhost:8765 without
a console. It reuses an existing server. `run_app.bat` remains a compatibility
launcher. For development, run `python app/backend/server.py` from the repo root.
Set `SMARTDOC_MODEL` before starting to override the summarisation and identifier
detection model. `SMARTDOC_VISION_MODEL` overrides the vision fallback. Scanned pages normally use
the dedicated Swedish OCR engines described below.

The app folder can run independently of the research corpus. Bundled historical
physician references and codebooks are no longer read by the processing pipeline.

## Processing documents

1. Upload up to 10 PDF, DOCX, TXT, PNG/JPG, or transcription JSON files for the
   same patient, with a combined limit of 64 MB, or paste text. Selection order
   is retained. All documents contribute to one summary and one history entry.
2. Optionally enter extra names or identifiers to replace, one per line. These
   exact strings supplement automatic detection; they are not a substitute for
   reviewing the pseudonymised output.
3. Native text is extracted first and checked for incomplete image coverage.
   Scans use Swedish OCR, with local vision as a last resort.
   Swedish rules and a local model identify names, identity numbers, contact
   details, dates, ages, organisations and locations. Only exact source spans
   from supported categories are accepted from the detector.
4. Consistent typed placeholders such as `[NAME_PATIENT_MALE_01]` replace the
   detected identifiers throughout the batch. A sex-specific name placeholder
   is requested only when sex is explicit in the source. Different spellings
   or aliases of a person are not guaranteed to resolve to the same placeholder.
5. The summariser receives the pseudonymised text, with source IDs. Each summary
   sentence must cite existing source IDs. The output is limited to 200 words,
   with up to eight supporting references per sentence and eight short uncertainty
   notes. An incomplete response triggers up to two automatic retries with more
   output space; invalid formatting or references allow one separate repair attempt.
   These retries reuse the completed transcription and pseudonymisation. The
   identifier mapping stays encrypted until restoration, then is deleted; it is
   also deleted if the job ultimately fails. Truncated summaries are never saved.
   The context window grows automatically for longer records, up to the smaller
   of 131,072 tokens and the model's reported capacity. Ollama uses its actual
   tokenizer to reject overflowing input; the app retries a larger window when
   possible. Input trimming and context shifting are disabled explicitly.
   Generation instructions preserve negation, uncertainty,
   time status, doses and units; clinicians still need to check the result.
6. Original values replace the placeholders present in the summary and its
   uncertainty notes. The model may omit identifiers that are not relevant to
   the summary. The temporary encrypted mapping is then deleted.

Open **Pseudonymised source and references** to inspect the extracted text using
its source IDs. Existing IDs show where a statement points; they do not prove
that its interpretation is correct. Numeric discrepancy checks are diagnostic,
not a medical factuality score. Detector failures and possible remaining
identifiers appear in the review notes.

## Longer patient records

The app selects an 8K-128K context window according to the combined pseudonymised
record, instructions, source IDs, and output allowance. It asks Ollama for the
selected model's native capacity and never requests more than that capacity.
The size estimate only selects a starting window: it no longer rejects a record
based on character count. The model's actual tokenizer decides whether it fits.

Ollama 0.32.1 or newer is required for the verified no-truncation controls.
Oversized requests retry at a larger window within the configured limit. If the
complete record still cannot fit, the app stops explicitly; it does not produce
a summary from a shortened record. Memory failures are reported without retrying
with even larger allocations. Short records continue to use smaller windows.

Generation starts with a 4,096-token output allowance and can retry with 8,192,
then 16,384 tokens when the model stops before finishing. This allowance includes
JSON, source references, and uncertainty notes; the summary remains at most 200
words. Context sizing reserves room for this output and uses measured input token
counts from earlier attempts when available. References and uncertainty notes are
bounded in both the requested schema and response validation. Run telemetry records
each attempt's token counts and outcome; incomplete-response logs contain counters,
not journal or generated text. If all attempts fail, retrying the failed job from
the start requires document preparation again because its mapping has been deleted.

For a machine with enough memory, the ceiling can be raised before starting
SmartDoc (Gemma 4 12B reports a native limit of 262,144 tokens):

```powershell
$env:SMARTDOC_SUMMARY_MAX_CONTEXT = "262144"
python app/backend/server.py
```

The default remains 131,072. This setting covers the entire context, including
instructions and generated output, so it is not an exact source-token allowance.
It does not change the 10-document, 64-MB or 500-page PDF limits. A larger window
uses more memory and may take longer, especially with five Ollama slots. The
256K ceiling is available but has not been established as a safe memory setting
or a quality target on every workstation. Source inclusion does not guarantee
that a 200-word summary captures every relevant fact.

See [LONG_RECORD_RESEARCH.md](LONG_RECORD_RESEARCH.md) for the literature review,
architecture decision, limitations, and local verification evidence. A manual
synthetic capacity test is available as `app/tests/benchmark_summary_context.py`;
run it with `--output <folder>` to save its generated source and results. It uses
the local model, can take several minutes, and measures feasibility rather than
clinical accuracy.

## Reading scanned documents

The reader uses Swedish Tesseract 5 and PaddleOCR 3.7 with the PP-OCRv6 medium
recognition and detection models. PDF pages are rendered at 300 DPI (with a
14-megapixel cap). EXIF orientation, document rotation, small skew and dark
backgrounds are handled before OCR. Original input files remain unchanged.

- **Balanced** is the default: Tesseract first, then PaddleOCR for uncertain
  words, uncertain critical tokens, or detected table layouts.
- **Thorough cross-check** uses both readers on every scanned page. It compares
  numbers, units, dates, negations, wording and reading order, and displays
  disagreements. This is the
  option to use when an extra check is more important than throughput.
- **Fast** prioritises Tesseract and does not request an additional reader merely
  because a table was detected. Low-confidence results still receive extra checks.

A successful short page is accepted without length-based retries. Known vision
truncation and repetition are rejected, with at most two vision attempts. Repeated
clinical lines are preserved. A nonblank page that fails extraction stops the job;
blank pages do not create fake text from page markers. Up to five pages are read
concurrently, preserving source order. A shared limit prevents concurrent files
or jobs from multiplying the number of active OCR pages. Image uploads and Word
images run concurrently too. PDF objects are accessed only by the caller thread;
their rendered pages are sent to the OCR workers. Identical raster pages within one PDF reuse
their OCR result in memory for that file only. No persistent OCR text cache is made.

PaddleOCR uses up to five isolated worker processes with individual timeouts.
Workers are started only when needed and reused afterwards. First use loads local
models and is slower; five simultaneous first requests also use more memory. Tesseract works without it.
Normal OCR reads local model files without fetching models or uploading images.
The health indicator reports installed OCR availability. Restart after setup:

```powershell
powershell -ExecutionPolicy Bypass -File app/setup_ocr.ps1
```

`-SkipPaddle` installs the lightweight Tesseract path only. The full setup uses
winget for Tesseract when needed, with a verified direct Windows installer from
the [Tesseract release](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3)
when winget is missing or cannot install it. The direct installer is checked
against its pinned SHA256 and uses a dedicated per-user Tesseract directory.
Setup downloads official Swedish/English/orientation
language files, and installs the optional pinned packages in `requirements-ocr.txt`.
It reuses existing PaddleOCR models, otherwise downloads them during setup.
Setup verifies all three language files and runs a synthetic Swedish reading
test before reporting success. Missing Tesseract or failed verification stops
setup with an error. The full setup passes the same Python executable to the OCR
step so packages are installed into the Python environment it just checked.
`SMARTDOC_TESSERACT`, `SMARTDOC_TESSDATA` and `SMARTDOC_OCR_MODELS` support custom
local locations. The checked model files must be present before runtime use.

Per-page engine, timing, orientation and reading warnings are saved in run details.
Warnings also appear in summary and pseudonymised-document exports. Confidence is
an engine-specific routing signal, not a clinical accuracy probability. Readers may
agree and still be wrong. Column and table reconstruction is heuristic; handwriting,
complex forms, overlays and damaged scans require comparison with the original.

DOCX extraction retains table row/cell boundaries and includes embedded raster
images. Headers, footers and package metadata are excluded. Unsupported drawings
produce a request to export to PDF rather than silently omitting their content.
UTF-8, UTF-16 with BOM and Windows-1252 text inputs are supported. PDFs over 500
pages and images over 80 megapixels are rejected with a clear message.

## Five-page processing

OCR and identifier detection each allow up to five simultaneous tasks. Identifier
checks respect page/document boundaries; long pages and pasted text are divided
into bounded passages. All detections are joined before a single mapping is
assigned across the entire batch. Summary generation starts only after every
transcription and identifier task has finished. Failed identifier checks retain
the rules-based fallback and review warning; unreadable input pages stop the job.
The combined mapping is encrypted temporarily and removed after restoration.

Ollama must also allow concurrent requests, otherwise it queues the five requests
and runs them one at a time. Setup configures the Windows user setting. On an
existing installation, run this once and restart Ollama after active jobs finish:

```powershell
powershell -ExecutionPolicy Bypass -File app/configure_parallel.ps1
```

This sets `OLLAMA_NUM_PARALLEL=5` for the local Ollama server. It affects other apps
using that same server too. On other platforms, set that environment variable for
`ollama serve` and restart the service. See the [Ollama concurrency documentation](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).
Five requests use more context memory and do not guarantee a fivefold speedup.

## Preview and download

After a run, select **Summary**, **Pseudonymised documents**, or **Both**, then
choose Word, PDF, TXT, or Markdown. Both downloads a ZIP containing two files
in the chosen format. PDF previews the actual PDF. TXT and Markdown preview the
text. Word shows a local content preview; download the DOCX to view Word's exact
page layout. No external document viewer receives the content.

The pseudonymised download contains extracted text and source IDs. It is a new
text document: original page images, document package metadata, and the mapping
are not copied into it. It is not a redacted copy of the original PDF or scan.
Automatic detection can miss identifiers, including names and addresses, so
review it before sharing. Older history entries can export their summary but
must be processed again to obtain a pseudonymised document.

## Identifier mapping and local storage

Each job encrypts its mapping with authenticated Fernet encryption before
writing `app/data/private/<job-id>.enc`. A separate random key is held only in
the running process. The mapping and key are not included in history, API
responses, exports, or Git. Normal completion and handled errors delete the
mapping file. A process or machine crash can leave ciphertext with no retained
key; the job must be run again. This is temporary restoration within a run,
not a persistent patient codebook. Python does not guarantee memory zeroisation.

**Mapping encryption does not encrypt the whole app's data.** Original uploads
remain in `app/data/uploads/`. Final restored summaries, input filenames and
history labels are stored as plain local JSON in `app/data/history/`. Existing
historical records are not rewritten. Deleting a history entry does not remove
its original upload files. Protect the workstation and its backups accordingly.
The identifier detector and summariser use local Ollama; the detector necessarily
reads the original text. No patient text is sent to Git or an online viewer.

## App updates

With Git and the full clone, the backend checks the branch tracked on `origin`
at startup and every 30 minutes. **Check for updates** checks immediately.
Checking reads the remote version without changing the checkout. When an update
is available, click **Download and install** in the bottom notification. The
backend fetches and installs the latest remote commit, installs changed Python requirements,
checks imports, and restarts the hidden server. The page reconnects and reloads.

Running summaries block installation. Local code edits and local commits do not
require a manual commit or merge: the downloaded app code replaces them. The
updater first keeps tracked edits in a local Git stash and the previous commit
under `refs/smartdoc-backups/`. Untracked files stay in place unless they conflict
with new app files; conflicting files are moved to `app/data/update-backups/`.
These backups stay on the computer and are never pushed to GitHub.

Patient data in `app/data/`, including locally changed legacy tracked records,
is excluded from the code backup and replacement. Local environment settings and
unrelated untracked files are preserved. Releases changing tracked `app/data/`
files still require a manual update to protect existing records. A failed install
or preflight restores the previous Git checkout and backed-up edits without
restarting; packages already changed by pip are not rolled back. A copied app
without Git still runs but cannot use this updater. Logs are stored under
`app/data/logs/`.

If an older installation is stuck on "Save or commit local changes", replace
its updater once. In PowerShell, from the `Summary_app` repository folder, run:

```powershell
git fetch origin
if ($LASTEXITCODE -eq 0) {
    git restore --source=origin/main --worktree -- app/backend/updater.py
}
```

This replaces only the old updater file. Restart that computer to stop the old
background server, open SmartDoc, and click **Download and install**. Subsequent
updates use the normal in-app button without this repair step.

## Research and verification

See [TRANSCRIPTION_VERIFICATION.md](TRANSCRIPTION_VERIFICATION.md) for measured
synthetic OCR checks, and [RESEARCH_NOTES.md](RESEARCH_NOTES.md) for the supplied thesis findings,
implementation choices, and remaining evaluation work. No study winner or
clinical quality score is claimed for this app configuration. Evaluation-only
physician summaries are not generation examples.

From the repo root:

```powershell
python -m pip install -r app/requirements-dev.txt
python -B -m unittest discover -s app/tests -p test_transcription.py -v
python -B -m unittest discover -s app/tests -p test_privacy_exports.py -v
python -B -m unittest discover -s app/tests -p test_improvements.py -v
python -B -m unittest discover -s app/tests -p test_parallel.py -v
python -B -m unittest discover -s app/tests -p test_summary_context.py -v
python -B -m unittest discover -s app/tests -p test_summary_recovery.py -v
python -B -m unittest discover -s app/tests -p test_ocr_setup.py -v
node --check app/frontend/app.js
```

Windows installer regression checks use Pester with mocked downloads/installers:

```powershell
Invoke-Pester app/tests/setup_ocr.Tests.ps1
```

The tests use synthetic text, model mocks and temporary Git remotes. They cover
batch validation, reversible placeholders, encrypted mapping cleanup, source
references, export contents, local API errors, update rollback and an actual
server restart. The historical `test_deanonymization.py` requires the original
external study corpus and tests a legacy module outside the new pipeline.
