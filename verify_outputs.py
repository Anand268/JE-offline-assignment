"""Check generated artifacts against source bytes/pixels, not transcription accuracy."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops
import pymupdf

from app import load_results, save_artifact, save_bundle


def verify(source: Path, output: Path):
    result = load_results(output / "results.json")
    original = source.read_bytes()
    assert original == (output / "source.pdf").read_bytes(), "Stored PDF differs from input"
    assert hashlib.sha256(original).hexdigest() == result["source"]["sha256"], "Source hash mismatch"
    validator = Draft202012Validator(json.loads((output / "candidate.schema.json").read_text(encoding="utf-8")))
    items = result["candidates"]
    for c in items:
        validator.validate(c)
        assert c["raw_text"] == c["value"], "Notation changed between raw reading and value"
        assert c["status"] == "unapproved" and c["review_status"] == "human_review_required"
    with (output / "candidates.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(items), "CSV row count mismatch"
    for row, c in zip(rows, items):
        assert all(row[k] == str(c[k]) for k in ("id", "page", "raw_text", "value", "status", "review_status")), "CSV altered reading/status"
    scale = result["config"]["dpi"] / 72
    checked = 0
    with pymupdf.open(source) as doc:
        assert len(doc) == result["source"]["page_count"] == len(result["pages"])
        for page, meta in zip(doc, result["pages"]):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
            render = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            with Image.open(output / meta["source_image"]) as saved:
                assert render.size == saved.size and ImageChops.difference(render, saved).getbbox() is None, "Source page image mismatch"
            for c in (c for c in items if c["page"] == meta["page"]):
                x0, y0, x1, y1 = c["bbox"]
                assert 0 <= x0 < x1 <= page.rect.width and 0 <= y0 < y1 <= page.rect.height
                box = (max(0, math.floor(x0 * scale) - 12), max(0, math.floor(y0 * scale) - 12),
                       min(render.width, math.ceil(x1 * scale) + 12), min(render.height, math.ceil(y1 * scale) + 12))
                expected = render.crop(box)
                with Image.open(output / c["evidence_image"]) as actual:
                    assert actual.size == expected.size and ImageChops.difference(expected, actual).getbbox() is None, f'Evidence mismatch: {c["id"]}'
                checked += 1
    assert checked == len(items)
    return {"artifact_checks": "passed", "source_sha256": result["source"]["sha256"],
            "page_count": len(result["pages"]), "candidate_count": len(items),
            "evidence_crops_checked": checked, "csv_rows_checked": len(rows),
            "observation_count": result["summary"]["observation_count"],
            "conflicting_candidates": sum(bool(c["conflicting_candidate_ids"]) for c in items),
            "low_confidence_candidates": sum("low_ocr_confidence" in c["review_reasons"] for c in items),
            "approved_count": sum(c["status"] == "approved" for c in items),
            "vision_enabled": result["config"]["vision_enabled"],
            "transcription_accuracy": "not measured; requires source review"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--desktop", action="store_true", help="Exercise the real withdrawn desktop with these results")
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--inspection-dir", type=Path)
    parser.add_argument("--examples", nargs="*", default=[])
    args = parser.parse_args()
    checked = verify(args.source, args.output)
    result = load_results(args.output / "results.json")
    examples = [c for c in result["candidates"] if c["id"] in args.examples]
    assert len(examples) == len(set(args.examples)), "Unknown example ID"
    if args.inspection_dir:
        args.inspection_dir.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(args.source) as doc:
            for c in examples:
                page = doc[c["page"] - 1]
                # Render independently at 576 DPI with context; no OCR preprocessing.
                bounds = pymupdf.Rect(c["bbox"]) + (-5, -5, 5, 5)
                bounds &= page.rect
                pix = page.get_pixmap(matrix=pymupdf.Matrix(8, 8), clip=bounds, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                image.rotate(c["rotation_ccw"], expand=True).save(args.inspection_dir / f'{c["id"]}-source.png')
        checked["inspection_examples"] = [{k: c[k] for k in ("id", "page", "bbox", "raw_text", "review_status")} for c in examples]
    if args.desktop:
        import tkinter as tk
        from app import App
        window = tk.Tk()
        window.withdraw()
        try:
            app = App(window)
            app.open_results(args.output / "results.json")
            window.update()
            assert len(app.table.get_children()) == len(result["candidates"])
            for c in examples:
                app.table.selection_set(c["id"])
                window.update()
                app.show_candidate()
                assert c["raw_text"] in app.details.get("1.0", "end")
                assert app.photo is not None
            assert not app.remote.get()
            checked["desktop_result_access"] = "passed"
            checked["desktop_examples_displayed"] = len(examples)
        finally:
            window.destroy()
    if args.export_dir:
        import zipfile
        args.export_dir.mkdir(parents=True, exist_ok=True)
        for name in ("results.json", "candidates.csv"):
            save_artifact(args.output, name, args.export_dir / name)
            assert (args.export_dir / name).read_bytes() == (args.output / name).read_bytes()
        archive_path = args.export_dir / "evidence.zip"
        save_bundle(args.output, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            assert archive.read("source.pdf") == args.source.read_bytes()
            assert all(c["evidence_image"] in archive.namelist() for c in result["candidates"])
        checked["sample_exports"] = "JSON, CSV and ZIP passed"
    report = json.dumps(checked, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    # Windows legacy consoles may not encode all OCR symbols. The saved report
    # remains verbatim UTF-8; escape only the console's JSON representation.
    print(json.dumps(checked, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
