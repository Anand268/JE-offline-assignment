# Evidence-first PDF extraction

Local desktop workflow and CLI implementation of `APPROVED_DESIGN.md`. The workspace uses Python, JSON/CSV artifacts, and a static review report; there is no web UI framework or HTTP API convention to extend. The desktop uses Python's bundled Tkinter and the existing CLI as a worker process. No web server or additional packages are required.

## Setup and dependencies

Source PDFs and generated results are kept local and excluded from Git. After cloning, place your authorized sample at `input-folder/3D-Drawing.pdf` before running the demo. References to supplied-sample results in the test report describe local verification; those artifacts are regenerated rather than distributed in this repository.

Tested environment: Windows, Python **3.11.0**, CPU OCR, with Tcl/Tk **8.6** installed. This is the measured environment, not a cross-platform compatibility claim. Start from the repository directory:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
```

If `.venv` already exists, reuse it. Activation is optional when using `.\.venv\Scripts\python.exe` directly, as the commands below do. `requirements.lock.txt` pins the tested environment; `requirements.txt` declares broader version ranges, whose future combinations have not all been tested. Installing dependencies needs package-index access. The default extraction needs no API credentials or remote inference; RapidOCR includes its ONNX models in the installed package.

| Dependency | Implemented use |
|---|---|
| PyMuPDF | PDF validation, native text, vector/image inspection, rendering |
| OpenCV, NumPy | Grayscale preprocessing, image arrays and rotation |
| RapidOCR ONNX Runtime, ONNX Runtime | Local text detection, orientation classification and recognition on CPU |
| Pillow | Source crops, annotations, desktop previews and pixel verification |
| Pydantic | Config/candidate validation and strict optional model-response validation |
| jsonschema | Generated candidate-schema verification |
| OpenAI SDK, python-dotenv | Optional remote adapter and local credential loading |
| Tkinter, unittest, subprocess, CSV/JSON/ZIP libraries | Python standard-library desktop, tests, worker process and artifacts |

`pdfplumber`, `pytesseract`, `pandas`, and `rapidfuzz` remain in the originally installed requirements but are **not used by this slice**. The active OCR engine is RapidOCR; Tesseract is not required. Using the unused `pytesseract` package later would require a separate system Tesseract executable. No additional web framework, database, queue, or storage service is installed.

## Run

In PowerShell, from this directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

1. **Choose PDF** or enter its path, then **Validate** to check format, size, page count, encryption, and configured resource limits.
2. **Extract locally** starts a separate process; the window remains responsive and shows page/tile progress. **Cancel extraction** stops it, retains partial files, and does not mark partial results as complete.
3. Select a candidate to see its verbatim reading, coordinates, review reasons, conflicts, and source crop. **Open source page** shows the full page. Search by reading, candidate ID, or review reason. Readings remain unapproved; the UI does not provide an approval action.
4. **Save JSON**, **Save CSV**, or **Save all evidence (ZIP)** exports artifacts through a save dialog. The ZIP includes the original PDF, source/evidence images, observations, configuration, schema, raw text, and HTML report. Export outside the stored result directory so the source artifacts stay unchanged.

Use **Open saved results** and select `output-folder/3D-Drawing/results.json` to inspect the supplied sample without re-running OCR. New desktop jobs use unique `output-folder/run-<id>/result/` directories and preserve prior runs. Local processing is the default for every desktop run, even if `vision_enabled` is true in the CLI config. Extraction failures are shown in the status/log area and do not load partial results.

Tkinter needs the Tcl/Tk component of a standard Python installation. If it is missing, enable Tcl/Tk in the Python installer. Desktop integration tests need a desktop session and access to that runtime; restricted sandboxes can prevent Tk from loading otherwise installed runtime files.

The CLI remains available:

```powershell
.\.venv\Scripts\python.exe pipeline.py input-folder/3D-Drawing.pdf --output output-folder/new-run --config config.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Use a **new or empty output directory** for each run. Existing results are never overwritten. Open the chosen output directory's `review.html` in a browser to compare candidates with source crops. Click a crop to see its full source page. Red rectangles and candidate IDs are also saved in `pages/*-annotated.png`.

The historical `3D-Drawing` and `verification-run` directories already contain results in this workspace. To reproduce the demo without overwriting them:

```powershell
.\.venv\Scripts\python.exe pipeline.py input-folder/3D-Drawing.pdf --output output-folder/demo-run --config config.json
.\.venv\Scripts\python.exe verify_outputs.py input-folder/3D-Drawing.pdf output-folder/demo-run
.\.venv\Scripts\python.exe app.py
```

In the window, choose **Open saved results** and select `output-folder/demo-run/results.json`. For another run, choose another unused output directory. These are reproduction instructions; `demo-run` was not executed as part of this documentation update. The actual executed commands and directories are recorded in [TEST_REPORT.md](TEST_REPORT.md).

## Input and output contracts

Input is an existing `.pdf` file path plus a JSON configuration validated by `Config` in `pipeline.py`. There is no multipart upload endpoint. Default limits are **25 MiB** (`max_file_mb` uses 1024² bytes), **20 pages**, **240 DPI**, **25,000,000 rendered pixels per page**, and **100 OCR tiles per page**. Tiles are 1,400 pixels with 160-pixel overlap, processed at 0/90/180/270 degrees. The low-score threshold is 0.9. These are configured safeguards/heuristics, not measured optimal settings. `ocr_regions` uses 1-based page-number strings and displayed-page rectangles. Unknown keys, empty rotation lists, overlapping-tile settings with no forward step, invalid regions, unreadable/encrypted PDFs, and exceeded input limits are rejected. There is no measured global CPU/memory/time service limit.

`results.json` is the canonical UTF-8 JSON envelope, schema version `1.0`:

| Key | Contract |
|---|---|
| `source` | Original filename, SHA-256, byte size, page count and stored PDF path |
| `coordinate_system` | Displayed-page points and top-left coordinate convention |
| `config`, `versions` | Effective extraction settings and recorded package versions; no credential values |
| `pages` | Dimensions/rotation, native raw text, vector/image bounds, render paths and OCR coverage/tiles |
| `candidates` | Verbatim string readings with source locations, method, evidence, validation and human-review state |
| `assistance_audit` | Optional-model/human routing and bounded failure metadata |
| `review_queue` | Every candidate ID, including high-score OCR/native text |
| `warnings`, `summary` | Document/coverage warnings and observation/candidate/review/approval counts |

Candidate details and the complete field contract are in [APPROVED_DESIGN.md](APPROVED_DESIGN.md#candidate-contract). `candidate.schema.json` is generated from the actual Pydantic candidate model. There is no generated schema for the full result envelope yet. `process()` returns that envelope; the CLI prints progress and a summary and writes the full envelope to disk.

| Artifact | Contents |
|---|---|
| `source.pdf` | Unmodified input PDF bytes |
| `results.json`, `candidate.schema.json`, `config.effective.json` | Canonical result, candidate schema and effective config |
| `observations.json` | All pre-reconciliation readings, including duplicate/alternative observations; final IDs/crop paths are assigned later to candidates |
| `candidates.csv` | Selected candidate columns; nested values are JSON strings, UTF-8 with BOM |
| `raw_text.txt` | Page-native text and all OCR readings, with section markers |
| `pages/*.png` | Original page renders and separate annotated versions |
| `evidence/*.png` | Candidate crops with context; original page coordinates remain in JSON |
| `review.html` | Static review view and artifact links |

Desktop exports copy JSON/CSV unchanged or ZIP all result-directory artifacts. Exporting does not approve candidates. See [the architecture/service flow and deployment proposal](APPROVED_DESIGN.md#architecture-and-current-service-flow) for the boundary between this local implementation and proposed production services.

## What it does

1. Validates extension, PDF signature, size, readable/nonencrypted document, page count, and rendered pixel budget.
2. Inspects native text lines, vector paths, and embedded image bounds. Copies the original bytes to `source.pdf` and records SHA-256.
3. Extracts native text without numeric conversion. Renders relevant areas, uses OpenCV grayscale preprocessing, and runs bundled RapidOCR ONNX models locally on overlapping tiles at configured rotations. No Tesseract installation is required by this implementation.
4. Consolidates identical readings only at overlapping locations. Preserves all original readings in `observations.json`; overlapping disagreements remain separate, cross-linked candidates. Confidence is an OCR model score, not a calibrated probability of correctness.
5. Produces source crops, full source images, annotated images, JSON, CSV, raw text, a candidate JSON schema, and an HTML review report. **Every candidate stays unapproved and requires human review.** No approvals are implemented in the static report.

Each candidate has a string `raw_text` and identical string `value`, page, bounding box, OCR polygon where available, method, confidence, alternative observations, conflicts, evidence paths, and review reasons. Coordinates use the **displayed page**, top-left origin, in PDF points (72 points/inch), including PDF page rotation. OCR rotation is counterclockwise relative to the source crop. Crops have a 12-pixel context margin; polygons/bounds refer to detected text, not that margin. Native bounds are axis-aligned line rectangles. No geometry association is assigned.

All notation is retained exactly as returned by the native parser or OCR engine: no float parsing, zero stripping, decimal rounding, symbol replacement, or tolerance normalization. **OCR can itself misread, omit, or invent characters.** Its output is a candidate transcription, never a verified value. Source pixels and competing readings are the authority for human review. Candidate discovery cannot establish that every drawing annotation was found. No material, units, nominal dimensions, tolerance semantics, or dimension-to-geometry inference is implemented.

CSV stores values as strings, but spreadsheet programs may automatically convert numeric notation. Import `raw_text` and `value` columns explicitly as **Text**; JSON is the canonical machine-readable output. Raw CSV values are not escaped with extra characters because that would change notation; use a text-aware importer for untrusted PDF text (spreadsheet formula execution is possible).

## Configurable assumptions and limits

`config.json` controls input limits, render DPI, OCR rotations, tile size/overlap, tile budget, uncertainty threshold, explicit OCR regions, and optional vision budget. Unknown configuration keys are rejected. Rendering is bounded per page; OCR tiles are capped per page and any truncation is flagged. This local tool is intended for trusted PDFs, not a hardened hostile-file processing service.

The supplied drawing has one portrait page, no native text or embedded raster images, and about 10,000 vector paths. Its lettering is outlined. Therefore this sample uses whole-page tiled OCR in four orientations. **There are no hard-coded title-block, material, dimension, or geometry regions.** Pages with native text also receive OCR if vector paths are present, to avoid assuming all lettering is native. Otherwise OCR targets embedded images. To override regions, set `ocr_regions` to e.g. `{"1": [[0, 0, 300, 400]]}` using displayed-page points. An empty list explicitly disables OCR for that page; restricting regions reduces coverage and must be reviewed. Native parsing still covers the entire page.

Vector paths are inspected and recorded, not interpreted as CAD geometry or reconstructed into text. OCR detection misses, clipped tile-boundary text, tiny fonts, rotated callouts, diameter symbols, stacked tolerances, and linework touching lettering remain limitations. Overlap helps but does not eliminate them. Conflicts are a spatial overlap heuristic and can include neighboring text; every original observation remains available.

## Optional selective vision assistance

By default no PDF content leaves the machine. The only supported trusted provider is explicitly configured `vision_provider: "openai"`, with a nonempty `vision_model` supporting image input and JSON responses, and `OPENAI_API_KEY` in the environment or local `.env`. The endpoint is pinned to `https://api.openai.com/v1`; ambient `OPENAI_BASE_URL` cannot redirect document crops. Other provider names are rejected. Credentials are never saved in result configuration.

For the CLI, also set `vision_enabled: true`. For the desktop, restart after configuring the provider, model and credentials, then explicitly check **Use configured remote vision for this run**. The checkbox is unchecked initially and unavailable if configuration or credentials are missing. Local OCR can always be used without provider access. No API request is made merely to check configuration.

Only low-confidence or conflicting OCR crops are selected, capped by `max_vision_regions` and `max_vision_output_tokens` per request, with no automatic retries and a 45-second request timeout. The budget bounds request count and requested output tokens, not exact monetary cost. Regions with no detected text are not automatically discovered by the model and require human inspection.

Vision returns a separately labeled `vision_proposal`; it never replaces OCR/native text or source evidence. A strict response schema requires a string/null transcription and boolean uncertainty, forbids extra keys, and bounds transcription length. Incomplete/truncated responses are rejected. Accepted proposals have forced `unapproved` and `human_review_required` statuses regardless of model content. Invalid responses or API failures fall back to human review and are recorded in `assistance_audit`.

**Live provider integration is untested.** No provider-access check or real model call was made during implementation. Unit tests mock provider responses to test schema rejection, request budgets, pinned endpoint configuration and fallback; these are not evidence of a successful provider integration. The supplied sample results were generated entirely locally.

The adapter's [exact runtime system prompt and request/response contract](APPROVED_DESIGN.md#implemented-model-adapter-and-exact-runtime-prompt) are documented from `pipeline.py`. The prompt was passed to mocked clients during tests, **not executed by a live vision-language model**. Local OCR models were actually executed on the supplied PDF.

## Measured results, limitations and remaining work

The most recent executed suite passed **20 tests, none skipped** (`4.690s`, exit 0); this is not a performance benchmark. The sample extraction and final artifact/desktop/export verification also exited 0. Recorded sample inspection: **1 page, 0 native text lines, 0 embedded raster images, 9,960 vector paths, 501 OCR observations, 399 unapproved candidates**. **376** candidates have overlapping conflicts and **309** fall below the OCR threshold; those groups overlap. All **399** crops matched source-render pixels and all **399** CSV rows matched JSON. No OCR accuracy percentage, throughput, memory profile, or completeness score has been measured.

Manual spot checks recovered matching candidates for `DRAFT`, `17.05.2024`, and `1.8±0.2`; they also found `Measured` misread as `Heasured` and `5.3±0.05` misread in a corrupted alternative as `C.3±1.05（6x）/D`. Thus preserved notation means preservation of the extractor's reading, **not proof that OCR matched the source**. All readings still require review. Source crops, coordinates and commands are in [TEST_REPORT.md](TEST_REPORT.md). A verification-helper CP1252 console-print failure was fixed and the command rerun successfully; the report preserves that failure history.

Remaining work includes a labeled technical-drawing evaluation set, OCR improvements for tiny/outlined symbols and stacked tolerances, human correction/approval persistence, completeness tracking, and separately authorized live-provider testing. The [production deployment proposal](APPROVED_DESIGN.md#deployment-proposal-not-implemented-or-deployed) adds upload/authentication, durable jobs, isolated workers and controlled storage; none of those services has been built, deployed or load-tested. No material or dimension-to-geometry inference is promised by that proposal.

## Reproducibility

See [TEST_REPORT.md](TEST_REPORT.md) for executed test commands, supplied-PDF results, source comparisons, and known failures. `verify_outputs.py` checks source bytes, rendered pixels, all evidence crops, schemas, and CSV values; its optional `--desktop` and export flags exercise result access and actual sample exports. These artifact checks do not measure OCR transcription accuracy.

`results.json` records effective configuration, source hash, tool versions, source inspection, candidates, review queue, warnings, and assistance routing. No run timestamps or absolute output paths affect artifacts. Candidate IDs use stable page/location/text ordering. Re-run with the same PDF, configuration, locked dependencies, and CPU runtime to reproduce the local pipeline; different OCR/runtime releases or hardware can change readings. Remote vision outputs are inherently variable; stored proposals are the audit record. `requirements.lock.txt` captures the tested environment; install it with `python -m pip install -r requirements.lock.txt` for repeat runs.
