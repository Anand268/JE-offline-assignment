import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import zipfile

import pymupdf

from app import App, artifact_path, load_results, save_artifact, save_bundle
from pipeline import Config, process


class WorkflowTests(unittest.TestCase):
    def make_pdf(self, root):
        source = root / "native.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=100)
            page.insert_text((15, 30), "001.020 +/-0.005")
            doc.save(source)
        return source

    def test_result_loading_and_downloads_preserve_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_pdf(root)
            result_dir = root / "result"
            process(source, result_dir, Config(dpi=72))
            result = load_results(result_dir / "results.json")
            self.assertEqual(result["candidates"][0]["value"], "001.020 +/-0.005")
            save_artifact(result_dir, "results.json", root / "download.json")
            self.assertEqual((root / "download.json").read_bytes(), (result_dir / "results.json").read_bytes())
            save_bundle(result_dir, root / "evidence.zip")
            with zipfile.ZipFile(root / "evidence.zip") as archive:
                self.assertEqual(archive.read("source.pdf"), source.read_bytes())
                self.assertIn(result["candidates"][0]["evidence_image"], archive.namelist())
            with self.assertRaises(ValueError):
                artifact_path(result_dir, "../native.pdf")
            with self.assertRaises(ValueError):
                save_bundle(result_dir, result_dir / "self.zip")
            with self.assertRaises(ValueError):
                save_artifact(result_dir, "results.json", result_dir / "source.pdf")

    def test_desktop_extracts_displays_and_keeps_remote_off(self):
        # Real Tk event loop and real CLI child process, using a native-text fixture.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_pdf(root)
            window = tk.Tk()
            window.withdraw()
            try:
                with patch("app.RUNS_ROOT", root / "runs"):
                    app = App(window)
                    self.assertFalse(app.remote.get())
                    app.cfg = Config(dpi=72, vision_enabled=True)
                    app.path.set(str(source))
                    self.assertTrue(app.validate())
                    app.start()
                    deadline = time.monotonic() + 40
                    while app.worker and time.monotonic() < deadline:
                        window.update()
                        time.sleep(0.05)
                    self.assertIsNone(app.worker, "CLI job did not complete")
                    self.assertIsNotNone(app.result)
                    self.assertFalse(app.result["config"]["vision_enabled"])
                    self.assertEqual(len(app.table.get_children()), 1)
                    app.show_candidate()
                    self.assertIn("001.020 +/-0.005", app.details.get("1.0", "end"))
                    self.assertIn("SEPARATE REMOTE VISION PROPOSAL", app.details.get("1.0", "end"))
                    self.assertIsNotNone(app.photo)
                    app.search.set("not present")
                    self.assertEqual(len(app.table.get_children()), 0)
                    app.path.set(str(root / "missing.pdf"))
                    self.assertFalse(app.validate())
            finally:
                if 'app' in locals() and app.worker:
                    app.worker.terminate()
                    app.worker.wait(timeout=5)
                window.destroy()

    def test_result_access_rejects_missing_invalid_and_escaped_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_pdf(root)
            result_dir = root / "result"
            process(source, result_dir, Config(dpi=72))
            path = result_dir / "results.json"
            original = json.loads(path.read_text(encoding="utf-8"))
            with self.assertRaises(FileNotFoundError):
                load_results(root / "missing.json")
            path.write_text("invalid JSON", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_results(path)
            path.write_text(json.dumps({**original, "schema_version": "unsupported"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_results(path)
            for evidence in ("evidence/missing.png", "../native.pdf", str(source.resolve())):
                changed = json.loads(json.dumps(original))
                changed["candidates"][0]["evidence_image"] = evidence
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                    load_results(path)
            changed = json.loads(json.dumps(original))
            changed["candidates"][0]["status"] = "approved"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_results(path)


if __name__ == "__main__":
    unittest.main()
