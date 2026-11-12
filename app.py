"""Local desktop workflow for the existing extraction CLI; no web server required."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import uuid
import webbrowser
import zipfile

from PIL import Image, ImageTk

from pipeline import Candidate, Config, validate_pdf, vision_credentials

WORKSPACE = Path(__file__).resolve().parent
RUNS_ROOT = WORKSPACE / "output-folder"


def artifact_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing artifact or path outside the result directory")
    return path


def load_results(path: Path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema_version") != "1.0":
        raise ValueError("Unsupported results format")
    for item in result["candidates"]:
        Candidate.model_validate(item)
        artifact_path(path.parent, item["evidence_image"])
        artifact_path(path.parent, item["source_image"])
    return result


def save_artifact(root: Path, relative: str, target: Path):
    source = artifact_path(root, relative)
    if source == target.resolve():
        raise ValueError("Choose a different destination; stored evidence is kept unchanged")
    if target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Save exports outside the stored result directory")
    shutil.copyfile(source, target)


def save_bundle(root: Path, target: Path):
    if target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Save the ZIP outside the stored result directory")
    files = [p for p in root.rglob("*") if p.is_file()]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(root).as_posix()
            archive.write(artifact_path(root, relative), relative)


class App:
    def __init__(self, window: tk.Tk):
        self.window = window
        window.title("PDF extraction and evidence review")
        window.geometry("1200x850")
        self.cfg = Config.model_validate_json((WORKSPACE / "config.json").read_text(encoding="utf-8"))
        self.events = queue.Queue()
        self.worker = None
        self.cancelled = False
        self.result_root = None
        self.result = None
        self.rows = {}
        self.photo = None
        self.output = None
        self.path = tk.StringVar(value=str(WORKSPACE / "input-folder/3D-Drawing.pdf"))
        self.status = tk.StringVar(value="Choose a PDF, validate it, then extract. Processing stays local by default.")
        self.remote = tk.BooleanVar(value=False)
        self.search = tk.StringVar()
        self._build()
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(150, self.poll)

    def _build(self):
        root = ttk.Frame(self.window, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="PDF extraction", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Readings remain unapproved. Compare source evidence before using any value.").pack(anchor="w", pady=(0, 10))
        file_row = ttk.Frame(root)
        file_row.pack(fill="x")
        self.path_entry = ttk.Entry(file_row, textvariable=self.path)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.choose = ttk.Button(file_row, text="Choose PDF…", command=self.choose_pdf)
        self.choose.pack(side="left", padx=6)
        self.validate_button = ttk.Button(file_row, text="Validate", command=self.validate)
        self.validate_button.pack(side="left")
        action_row = ttk.Frame(root)
        action_row.pack(fill="x", pady=8)
        self.extract = ttk.Button(action_row, text="Extract locally", command=self.start)
        self.extract.pack(side="left")
        self.cancel_button = ttk.Button(action_row, text="Cancel extraction", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=6)
        self.open_button = ttk.Button(action_row, text="Open saved results…", command=self.open_results)
        self.open_button.pack(side="left")
        self.remote_check = ttk.Checkbutton(action_row, text="Use configured remote vision for this run", variable=self.remote)
        self.remote_check.pack(side="left", padx=14)
        try:
            vision_credentials(self.cfg)
            provider_status = "Remote provider configured; not live-tested. Optional crops are sent only when checked."
        except ValueError:
            self.remote_check.configure(state="disabled")
            provider_status = "Remote vision unavailable: explicitly configure provider, model and credentials. Integration not live-tested."
        ttk.Label(root, text=provider_status, wraplength=1100).pack(anchor="w")
        ttk.Label(root, textvariable=self.status, wraplength=1100).pack(anchor="w", pady=6)
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x")
        self.log = ScrolledText(root, height=3, state="disabled", wrap="word")
        self.log.pack(fill="x", pady=6)
        downloads = ttk.Frame(root)
        downloads.pack(fill="x", pady=4)
        self.result_buttons = []
        for title, command in [("Save JSON…", lambda: self.save("results.json")),
                               ("Save CSV…", lambda: self.save("candidates.csv")),
                               ("Save all evidence (ZIP)…", lambda: self.save(None)),
                               ("Open review report", lambda: self.open_artifact("review.html")),
                               ("Open source page", self.open_page)]:
            button = ttk.Button(downloads, text=title, command=command, state="disabled")
            button.pack(side="left", padx=(0, 6))
            self.result_buttons.append(button)
        search_row = ttk.Frame(root)
        search_row.pack(fill="x", pady=6)
        ttk.Label(search_row, text="Find candidate:").pack(side="left")
        ttk.Entry(search_row, textvariable=self.search).pack(side="left", fill="x", expand=True, padx=6)
        self.search.trace_add("write", lambda *_: self.fill_table())
        split = ttk.Panedwindow(root, orient="vertical")
        split.pack(fill="both", expand=True)
        table = ttk.Frame(split)
        split.add(table, weight=2)
        columns = ("id", "page", "reading", "method", "score", "status")
        self.table = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", height=9)
        for key, label, width in [("id", "Candidate", 80), ("page", "Page", 45), ("reading", "Verbatim reading", 410),
                                  ("method", "Method", 70), ("score", "OCR score", 80), ("status", "Review", 200)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key in ("reading", "status"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_candidate)
        detail = ttk.Frame(split)
        split.add(detail, weight=3)
        ttk.Label(detail, text="Source evidence crop (rotated for readability when OCR specifies an orientation)").pack(anchor="w")
        self.image = ttk.Label(detail, text="Select a candidate to inspect its evidence.", anchor="center")
        self.image.pack(fill="x", pady=4)
        self.details = ScrolledText(detail, height=8, state="disabled", wrap="word")
        self.details.pack(fill="both", expand=True)

    def set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def choose_pdf(self):
        selected = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if selected:
            self.path.set(selected)
            self.validate()

    def validate(self):
        try:
            info = validate_pdf(Path(self.path.get()), self.cfg)
            self.status.set(f'Validated: {info["filename"]} — {info["page_count"]} page(s), {info["size_bytes"]:,} bytes. Ready to extract.')
            return True
        except Exception as exc:
            self.status.set(f"Validation failed: {exc}")
            return False

    def busy(self, active):
        for widget in (self.choose, self.validate_button, self.extract, self.open_button, self.path_entry):
            widget.configure(state="disabled" if active else "normal")
        self.cancel_button.configure(state="normal" if active else "disabled")
        self.remote_check.configure(state="disabled")
        if not active:
            try:
                vision_credentials(self.cfg)
                self.remote_check.configure(state="normal")
            except ValueError:
                pass

    def start(self):
        if self.worker or not self.validate():
            return
        try:
            cfg = self.cfg.model_copy(update={"vision_enabled": self.remote.get()})
            if cfg.vision_enabled:
                vision_credentials(cfg)
            run = RUNS_ROOT / f"run-{uuid.uuid4().hex}"
            run.mkdir(parents=True)
            config = run / "config.requested.json"
            config.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
            self.output = run / "result"
            self.worker = subprocess.Popen([sys.executable, "-u", str(WORKSPACE / "pipeline.py"),
                str(Path(self.path.get()).resolve()), "--output", str(self.output), "--config", str(config)],
                cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.cancelled = False
            self.result = None
            self.result_root = None
            self.fill_table()
            self.set_text(self.details, "")
            self.image.configure(image="", text="Extraction in progress…")
            for button in self.result_buttons:
                button.configure(state="disabled")
            self.set_text(self.log, "")
            self.busy(True)
            self.progress.start()
            self.status.set("Extracting — remote vision enabled for this run." if cfg.vision_enabled else "Extracting locally — no remote vision requests.")
            threading.Thread(target=self.read_progress, args=(self.worker,), daemon=True).start()
        except Exception as exc:
            self.status.set(f"Could not start extraction: {exc}")

    def read_progress(self, worker):
        with worker.stdout:
            for line in worker.stdout:
                self.events.put(("progress", line.rstrip()))
        self.events.put(("done", worker.wait()))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(value)
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                else:
                    self.worker = None
                    self.progress.stop()
                    self.busy(False)
                    if self.cancelled:
                        self.status.set("Cancelled. Partial files are retained for inspection; no completed results are loaded.")
                    elif value != 0:
                        self.status.set("Extraction failed. See the log above; no completed results are loaded.")
                    else:
                        self.open_results(self.output / "results.json")
        except queue.Empty:
            pass
        self.window.after(150, self.poll)

    def cancel(self):
        if self.worker and self.worker.poll() is None:
            self.cancelled = True
            self.worker.terminate()
            self.status.set("Cancelling extraction…")

    def open_results(self, path=None):
        if self.worker:
            return
        if path is None:
            chosen = filedialog.askopenfilename(initialdir=WORKSPACE / "output-folder", filetypes=[("Extraction JSON", "results.json")])
            if not chosen:
                return
            path = Path(chosen)
        try:
            result = load_results(path)
            self.result, self.result_root = result, path.parent
            self.search.set("")
            self.fill_table()
            for button in self.result_buttons:
                button.configure(state="normal")
            self.status.set(f'{len(result["candidates"])} unapproved candidates — human review required. {"; ".join(result["warnings"])}')
        except Exception as exc:
            messagebox.showerror("Cannot open results", str(exc))

    def fill_table(self):
        self.table.delete(*self.table.get_children())
        self.rows = {}
        if not self.result:
            return
        term = self.search.get().casefold()
        for c in self.result["candidates"]:
            if term and term not in f'{c["id"]} {c["raw_text"]} {" ".join(c["review_reasons"])}'.casefold():
                continue
            self.rows[c["id"]] = c
            self.table.insert("", "end", iid=c["id"], values=(c["id"], c["page"], c["raw_text"],
                c["extraction_method"], "—" if c["confidence"] is None else f'{c["confidence"]:.3f}', "Unapproved / human review"))
        children = self.table.get_children()
        if children:
            self.table.selection_set(children[0])
        else:
            self.set_text(self.details, "No matching candidates. Inspect the full source document for missed text.")
            self.image.configure(image="", text="No candidate selected")

    def show_candidate(self, _event=None):
        selected = self.table.selection()
        if not selected or selected[0] not in self.rows:
            return
        c = self.rows[selected[0]]
        text = (f'VERBATIM {c["extraction_method"].upper()} READING (unapproved):\n{c["raw_text"]}\n\n'
                f'Page {c["page"]}; bounds in displayed PDF points: {c["bbox"]}\n'
                f'Review reasons: {", ".join(c["review_reasons"])}\n'
                f'Conflicting candidates: {", ".join(c["conflicting_candidate_ids"]) or "None detected"}\n\n'
                'SEPARATE REMOTE VISION PROPOSAL (unapproved, not source evidence):\n'
                + (json.dumps(c["vision_proposal"], ensure_ascii=False, indent=2) if c["vision_proposal"] else "Not requested or unavailable."))
        self.set_text(self.details, text)
        try:
            with Image.open(artifact_path(self.result_root, c["evidence_image"])) as image:
                preview = image.convert("RGB").rotate(c["rotation_ccw"], expand=True)
                scale = min(3, 1050 / preview.width, 180 / preview.height)
                preview = preview.resize((max(1, int(preview.width * scale)), max(1, int(preview.height * scale))), Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(preview)
            self.image.configure(image=self.photo, text="")
        except Exception as exc:
            self.image.configure(image="", text=f"Evidence unavailable: {exc}")

    def open_artifact(self, relative):
        try:
            webbrowser.open(artifact_path(self.result_root, relative).as_uri())
        except Exception as exc:
            messagebox.showerror("Cannot open artifact", str(exc))

    def open_page(self):
        selected = self.table.selection()
        if selected:
            self.open_artifact(self.rows[selected[0]]["source_image"])

    def save(self, relative):
        name = relative or "extraction-evidence.zip"
        target = filedialog.asksaveasfilename(initialfile=name, defaultextension=Path(name).suffix)
        if not target:
            return
        try:
            if relative:
                save_artifact(self.result_root, relative, Path(target))
            else:
                save_bundle(self.result_root, Path(target))
            self.status.set(f"Saved {target}")
        except Exception as exc:
            messagebox.showerror("Cannot save results", str(exc))

    def close(self):
        if self.worker and self.worker.poll() is None:
            if not messagebox.askyesno("Extraction running", "Stop the extraction and close?"):
                return
            self.worker.terminate()
        self.window.destroy()


def main():
    window = tk.Tk()
    try:
        App(window)
    except Exception as exc:
        messagebox.showerror("Cannot start PDF workflow", str(exc))
        window.destroy()
        return
    window.mainloop()


if __name__ == "__main__":
    main()
