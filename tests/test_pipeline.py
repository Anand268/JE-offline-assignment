import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pymupdf
from pydantic import ValidationError

from pipeline import Config, VisionReading, assist, candidate, inverse_rotation, process, reconcile, validate_pdf, vision_credentials


class PipelineTests(unittest.TestCase):
    def test_notation_stays_verbatim(self):
        raw = "Ø001.020 ±0.005 / +0.02 −0.00 ° M6×1"
        c = candidate(1, [[1, 2], [50, 2], [50, 9], [1, 9]], raw, "ocr", 0.8)
        decoded = json.loads(c.model_dump_json())
        self.assertEqual(decoded["value"], raw)
        self.assertEqual(decoded["raw_text"], raw)
        self.assertEqual(decoded["status"], "unapproved")
        self.assertIsNone(decoded["geometry_association"])
        with self.assertRaises(ValidationError):
            type(c).model_validate({**decoded, "value": "1.02"})

    def test_rotation_coordinates_round_trip(self):
        w, h, x, y = 300, 200, 31, 42
        forward = {0: (x, y), 90: (y, w - x), 180: (w - x, h - y), 270: (h - y, x)}
        for rotation, point in forward.items():
            self.assertEqual(inverse_rotation(*point, w, h, rotation), (x, y))

    def test_reconciliation_retains_conflicts_and_distinct_locations(self):
        def make(text, x=0):
            return candidate(1, [[x, 0], [x + 10, 0], [x + 10, 5], [x, 5]], text, "ocr", 0.75)
        originals = [make("001.20"), make("001.20"), make("00120"), make("001.20", 100)]
        result = reconcile(originals, 0.9)
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(len(c.corroborating_observations) for c in result), 1)
        self.assertEqual(sum(bool(c.conflicting_candidate_ids) for c in result), 2)
        self.assertTrue(all(c.status == "unapproved" for c in result))
        self.assertTrue(all(c.id == "" for c in originals))

    def test_invalid_config(self):
        with self.assertRaises(ValidationError):
            Config(tile_size=256, tile_overlap=256)
        with self.assertRaises(ValidationError):
            Config(ocr_rotations=[45])

    def test_vision_budget_and_proposal_do_not_change_reading(self):
        c = candidate(1, [[0, 0], [10, 0], [10, 5], [0, 5]], "001.20", "ocr", 0.2)
        other = candidate(1, [[20, 0], [30, 0], [30, 5], [20, 5]], "002.00", "ocr", 0.3)
        candidates = reconcile([c, other], 0.9)
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content='{"transcription":"001.020", "uncertain":true}'))])
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "crop.png").write_bytes(b"mock-image")
            for item in candidates:
                item.evidence_image = "crop.png"
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch("openai.OpenAI") as factory:
                factory.return_value.chat.completions.create.return_value = response
                audit = assist(candidates, root, Config(vision_enabled=True, vision_provider="openai", vision_model="test-model", max_vision_regions=1))
                self.assertEqual(factory.return_value.chat.completions.create.call_count, 1)
                self.assertEqual(factory.call_args.kwargs["base_url"], "https://api.openai.com/v1")
            self.assertEqual(candidates[0].raw_text, "001.20")
            self.assertEqual(candidates[0].value, "001.20")
            self.assertEqual(candidates[0].vision_proposal["transcription"], "001.020")
            self.assertEqual(candidates[0].status, "unapproved")
            self.assertEqual(audit[1]["route"], "human")

    def test_vision_failure_routes_to_human(self):
        c = candidate(1, [[0, 0], [10, 0], [10, 5], [0, 5]], "001.20", "ocr", 0.2)
        candidates = reconcile([c], 0.9)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "crop.png").write_bytes(b"mock-image")
            candidates[0].evidence_image = "crop.png"
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch("openai.OpenAI") as factory:
                factory.return_value.chat.completions.create.side_effect = RuntimeError("simulated")
                audit = assist(candidates, root, Config(vision_enabled=True, vision_provider="openai", vision_model="test-model"))
            self.assertEqual(audit[0]["reason"], "vision_failed")
            self.assertEqual(candidates[0].status, "unapproved")

    def test_vision_requires_explicit_trusted_provider(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only", "OPENAI_BASE_URL": "https://untrusted.invalid"}):
            with self.assertRaises(ValueError):
                vision_credentials(Config(vision_enabled=True, vision_model="test"))
        with self.assertRaises(ValidationError):
            Config(vision_provider="untrusted")
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}), patch("dotenv.load_dotenv"):
            with self.assertRaises(ValueError):
                vision_credentials(Config(vision_provider="openai", vision_model="test"))

    def test_vision_response_schema_rejects_coercion_and_extra_fields(self):
        for raw in ('{"transcription":123,"uncertain":true}',
                    '{"transcription":"001","uncertain":"false"}',
                    '{"transcription":"001","uncertain":false,"approved":true}'):
            with self.assertRaises(ValidationError):
                VisionReading.model_validate_json(raw)

    def test_invalid_or_truncated_provider_responses_force_human_review(self):
        cases = [('not JSON', "stop"), ('{"transcription":', "stop"),
                 ('{"transcription":"001","uncertain":false,"approved":true}', "stop"),
                 ('{"transcription":"001","uncertain":"false"}', "stop"),
                 ('{"transcription":"001","uncertain":false}', "length")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "crop.png").write_bytes(b"mock-image")
            for raw, finish in cases:
                c = candidate(1, [[0, 0], [10, 0], [10, 5], [0, 5]], "001.20", "ocr", 0.2)
                items = reconcile([c], 0.9)
                items[0].evidence_image = "crop.png"
                response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
                    message=SimpleNamespace(content=raw))])
                with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch("openai.OpenAI") as factory:
                    factory.return_value.chat.completions.create.return_value = response
                    audit = assist(items, root, Config(vision_provider="openai", vision_enabled=True, vision_model="test-model"))
                self.assertEqual(audit[0]["reason"], "vision_failed")
                self.assertIsNone(items[0].vision_proposal)
                self.assertEqual(items[0].value, "001.20")
                self.assertEqual(items[0].status, "unapproved")

    def test_unreadable_model_transcription_stays_null_and_never_replaces_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "crop.png").write_bytes(b"mock-image")
            c = candidate(1, [[0, 0], [10, 0], [10, 5], [0, 5]], "0?1", "ocr", 0.1)
            items = reconcile([c], 0.9)
            items[0].evidence_image = "crop.png"
            response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=SimpleNamespace(content='{"transcription":null,"uncertain":true}'))])
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch("openai.OpenAI") as factory:
                factory.return_value.chat.completions.create.return_value = response
                assist(items, root, Config(vision_provider="openai", vision_enabled=True, vision_model="test-model"))
            self.assertIsNone(items[0].vision_proposal["transcription"])
            self.assertTrue(items[0].vision_proposal["uncertain"])
            self.assertEqual(items[0].value, "0?1")
            self.assertEqual(items[0].review_status, "human_review_required")
            self.assertEqual(items[0].vision_proposal["status"], "unapproved")

    def test_no_ocr_detections_does_not_invent_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with pymupdf.open() as doc:
                doc.new_page(width=100, height=100)
                doc.save(root / "unreadable.pdf")
            # Exercise the real pipeline's no-detections path deterministically.
            # This is a mocked engine result, not an OCR accuracy test.
            with patch("rapidocr_onnxruntime.RapidOCR") as engine:
                engine.return_value.return_value = (None, None)
                result = process(root / "unreadable.pdf", root / "result", Config(dpi=72, ocr_rotations=[0]))
                self.assertEqual(engine.return_value.call_count, 1)
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["summary"]["approved_count"], 0)
            self.assertTrue(any("full document requires human review" in w for w in result["warnings"]))

    def test_native_notation_survives_json_and_csv(self):
        raw = "Ø001.020 ±0.005 / +0.02 -0.00 ° M6×1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with pymupdf.open() as doc:
                page = doc.new_page(width=500, height=100)
                page.insert_text((15, 30), raw)
                doc.save(root / "notation.pdf")
            result = process(root / "notation.pdf", root / "result", Config(dpi=72))
            self.assertEqual(result["candidates"][0]["value"], raw)
            saved = json.loads((root / "result/results.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["candidates"][0]["raw_text"], raw)
            with (root / "result/candidates.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["value"], raw)

    def test_repeated_native_values_remain_distinct_across_locations_and_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with pymupdf.open() as doc:
                for _ in range(2):
                    page = doc.new_page(width=200, height=100)
                    page.insert_text((15, 25), "001.20")
                    page.insert_text((100, 65), "001.20")
                doc.save(root / "repeated.pdf")
            result = process(root / "repeated.pdf", root / "result", Config(dpi=72))
            items = result["candidates"]
            self.assertEqual(len(items), 4)
            self.assertEqual(len({c["id"] for c in items}), 4)
            self.assertEqual(len({(c["page"], tuple(c["bbox"])) for c in items}), 4)
            self.assertTrue(all(c["value"] == "001.20" and not c["conflicting_candidate_ids"] for c in items))

    def test_upload_boundary_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with pymupdf.open() as doc:
                doc.new_page(width=100, height=100)
                doc.save(root / "valid.pdf")
                doc.save(root / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
            (root / "wrong.txt").write_bytes((root / "valid.pdf").read_bytes())
            (root / "signature.pdf").write_bytes(b"not pdf")
            (root / "damaged.pdf").write_bytes(b"%PDF-1.7\ncorrupt")
            self.assertEqual(validate_pdf(root / "valid.pdf", Config())["page_count"], 1)
            for filename, exception in [("wrong.txt", ValueError), ("missing.pdf", ValueError),
                    ("signature.pdf", ValueError), ("locked.pdf", ValueError), ("damaged.pdf", pymupdf.FileDataError)]:
                with self.subTest(filename=filename), self.assertRaises(exception):
                    validate_pdf(root / filename, Config())
            with self.assertRaises(ValueError):
                validate_pdf(root / "valid.pdf", Config(ocr_regions={"1": [[0, 0, 101, 100]]}))

    def test_native_end_to_end_reproducible_and_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "native.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page(width=200, height=100)
                page.insert_text((15, 30), "001.020 +/-0.005")
                page.set_rotation(90)
                doc.save(source)
            cfg = Config(dpi=72)
            first = process(source, root / "first", cfg)
            second = process(source, root / "second", cfg)
            self.assertEqual(first, second)
            self.assertEqual(first["candidates"][0]["value"], "001.020 +/-0.005")
            self.assertEqual(first["summary"]["approved_count"], 0)
            self.assertEqual(first["candidates"][0]["extraction_method"], "native")
            box = first["candidates"][0]["bbox"]
            self.assertTrue(0 <= box[0] < box[2] <= 100)
            self.assertTrue(0 <= box[1] < box[3] <= 200)
            self.assertEqual((root / "first/source.pdf").read_bytes(), source.read_bytes())
            with (root / "first/candidates.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["value"], "001.020 +/-0.005")
            self.assertTrue((root / "first" / first["candidates"][0]["evidence_image"]).is_file())
            with self.assertRaises(ValueError):
                process(source, root / "first", cfg)

    def test_reject_invalid_pdf_before_creating_output(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "invalid.pdf"
            source.write_bytes(b"not a PDF")
            with self.assertRaises(ValueError):
                process(source, root / "out", Config())
            self.assertFalse((root / "out").exists())

    def test_resource_limits(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "pages.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.new_page()
                doc.save(source)
            for cfg in (Config(max_pages=1), Config(max_file_mb=0.00001), Config(max_page_pixels=1)):
                with self.assertRaises(ValueError):
                    process(source, root / "out", cfg)
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
