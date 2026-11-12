# Verification of the supplied drawing

Executed on 2026-11-12 from `C:\Users\DELL\Downloads\JE-Assignment`, using the project's Python 3.11 virtual environment. These results describe actual runs, not planned checks.

## Exact commands and results

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Exit **0**: **20 tests passed**, `Ran 20 tests in 4.690s`. The suite included a real withdrawn Tk window and real CLI child-process extraction of a synthetic PDF. It ran with access to the installed Tcl/Tk runtime outside the restricted sandbox. No tests were skipped. Provider responses and the no-OCR-detections test use explicit mocks; they are not live integrations or OCR accuracy tests.

Focused coverage includes:

- Exact string preservation of leading zeroes, decimal precision, diameter/degree/multiplication symbols, plus/minus, signed tolerances, and Unicode minus. Native PDF text also passes through real JSON/CSV output without conversion.
- Repeated `001.20` readings at two locations on each of two pages stay four distinct candidates. Conflicting overlapping readings remain separate and unapproved.
- No OCR detections yield no invented fields and a document-review warning. A mocked unreadable model transcription remains `null`; it never replaces the OCR reading `0?1`.
- Invalid JSON, truncated JSON/output, extra approval keys, incorrect field types, provider failures, missing credentials, untrusted provider names, call budgets, and the pinned endpoint.
- Invalid extension/signature, missing file, damaged PDF, encryption, byte/page/pixel limits, invalid OCR regions, and refusal to overwrite previous results.
- Desktop validation, progress/completion, candidate text/evidence display, filtering, and local-by-default behavior. Result loading/export also rejects missing files, invalid JSON/schema version, missing evidence, out-of-directory relative/absolute paths, and imported approved statuses.
- JSON byte-for-byte export and ZIP inclusion of original PDF/evidence. Exports cannot overwrite stored result artifacts.

```powershell
.\.venv\Scripts\python.exe pipeline.py input-folder/3D-Drawing.pdf --output output-folder/verification-run --config config.json
```

Exit **0**. The supplied PDF has **1 page, 0 native text lines, 0 embedded raster images, and 9,960 vector paths**. Six overlapping OCR tiles were processed in four orientations. Results: **501 observations, 399 candidates, 399 requiring human review, 0 approved**. Of these, **376** have overlapping conflicting readings and **309** are below the configured OCR score threshold; these groups overlap. These are candidate counts, not counts of correctly recovered fields. Remote vision was disabled.

Source SHA-256: `aca5545440c4ec64a9755860522fd6a1dfd2605722f9cc5911c019eab0089448`.

```powershell
.\.venv\Scripts\python.exe verify_outputs.py input-folder/3D-Drawing.pdf output-folder/verification-run --desktop --export-dir output-folder/verification-inspection/downloads --inspection-dir output-folder/verification-inspection --examples c00001 c00006 c00280 c00130 c00136 --report output-folder/verification-inspection/artifact-checks.json
```

Final exit **0**. This command ran with access to Tcl/Tk. It checked all **399 candidate schemas, CSV rows, evidence crops, and coordinate bounds**. The stored PDF matched the input bytes and hash. Full source images matched fresh renders from the original PDF; every crop matched the corresponding original-page pixels. The real desktop loaded all 399 rows, displayed all five selected examples and their images, and kept remote assistance unchecked. Actual sample JSON/CSV exports matched the stored bytes; the ZIP passed its CRC check and contained the original PDF and every evidence crop.

The first run of this last command exited **1** after completing its checks and writing its UTF-8 report: printing a full-width OCR parenthesis (`U+FF08`) raised a Windows CP1252 `UnicodeEncodeError`. `verify_outputs.py` was corrected to escape only console JSON while retaining verbatim UTF-8 in the saved report. The exact command above was rerun and passed. No extraction data was changed by that correction.

## Source inspection: recovered and unrecovered examples

I inspected the full source-page rendering and independently rendered source crops at 576 DPI, including a wider context crop around the failed tolerance. The following are manual spot checks, not an exhaustive transcription or accuracy measurement. Every example remains unapproved, including the readings that visually match. Competing alternatives are retained.

All locations below are on **page 1**, with bounding boxes in displayed PDF points `[x0, y0, x1, y1]`.

| Candidate | Source text inspected | Output reading | Assessment | Bounds |
|---|---|---|---|---|
| c00001 | `DRAFT` | `DRAFT` | Matching candidate recovered | `[494.4, 69.0, 520.5, 145.5]` |
| c00006 | `17.05.2024` | `17.05.2024` | Matching candidate recovered; other date readings conflict | `[521.1, 81.0, 536.7, 144.3]` |
| c00280 | `1.8±0.2` | `1.8±0.2` | Matching tolerance candidate recovered | `[431.1, 573.3, 453.6, 578.4]` |
| c00130 | `Measured` in `Measured on Snap 1-4` | `Heasured` | Incorrect initial letter; complete local label not recovered verbatim | `[317.4, 334.2, 331.8, 338.1]` |
| c00136 | Visible tolerance substring `5.3±0.05` | `C.3±1.05（6x）/D` | Incorrect nominal reading/tolerance and suffix; **not recovered correctly** | `[301.8, 337.8, 327.9, 341.7]` |

At the last tolerance location, overlapping alternatives `c00134 = 631.051ND` and `c00137 = 53±1.05（x7` are also incorrect; retaining alternatives did not recover the visible tolerance substring. The correct values in this table are human observations of source pixels and were **not** written back into candidates as approved values.

Independent source crops: [DRAFT](output-folder/verification-inspection/c00001-source.png), [date](output-folder/verification-inspection/c00006-source.png), [recovered tolerance](output-folder/verification-inspection/c00280-source.png), [misread label](output-folder/verification-inspection/c00130-source.png), [failed tolerance](output-folder/verification-inspection/c00136-source.png), [wider failed-region context](output-folder/verification-inspection/detail-source.png).

## Limits and failures

- **No automated test failures in the 20-test run.** The verification-helper console failure and successful rerun are documented above.
- OCR does not reliably recover this outlined technical drawing. High-confidence OCR can still be wrong; correct pixels and preserved strings do not establish transcription correctness. No completeness or field-accuracy percentage is claimed.
- Leading-zero and Unicode-minus preservation were tested with fixtures; this is not a claim that every such mark in the supplied drawing was recovered.
- No native text could be recovered from the sample because its lettering is outlined. Vector inspection does not decode those outlines into text.
- Material identification and dimension-to-geometry association are not implemented or inferred. Missing/unreadable values remain unresolved, with no invented replacements.
- **Live remote vision integration remains untested.** No document content was sent remotely. All provider tests use mocks and establish local validation/routing behavior only.

## Outputs and changes

Fresh [review report](output-folder/verification-run/review.html), [JSON](output-folder/verification-run/results.json), [CSV](output-folder/verification-run/candidates.csv), [raw text](output-folder/verification-run/raw_text.txt), [machine verification report](output-folder/verification-inspection/artifact-checks.json), and [tested evidence ZIP](output-folder/verification-inspection/downloads/evidence.zip).

Changed `tests/test_pipeline.py` and `tests/test_app.py`; added `verify_outputs.py` and this report; updated `README.md`. Generated fresh results under `output-folder/verification-run/` and inspection/export artifacts under `output-folder/verification-inspection/`. Earlier extraction results were preserved. No extraction or UI application logic changed in this verification task.
