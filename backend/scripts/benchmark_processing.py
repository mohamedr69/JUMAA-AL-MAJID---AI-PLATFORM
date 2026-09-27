"""Benchmark the document reading pipeline over a real project folder.

    venv\\Scripts\\python -m scripts.benchmark_processing --folder "<project folder>" --workers 3
    venv\\Scripts\\python -m scripts.benchmark_processing --folder ... --workers 2 --out bench-w2.json
    venv\\Scripts\\python -m scripts.benchmark_processing --folder ... --fresh-cache --limit 100

What the document processing job does to each file -- storage readiness,
the content hash, opening the PDF, classification, text extraction, the
deterministic parse, filled-box detection, OCR -- run through the same
code (document_processing.read_task in document_sync.read_in_completion_
order), over the folder's PDFs, with the same reader pool and the same
completion-order handling, and measured document by document. Nothing is
written to the platform's database and no model is called: the AI part is
measured from the processing job's own telemetry and the ai_usage rows.

Prints, and with --out writes as JSON: documents, duration, documents per
minute; median, P90, P95 and P99 per-document time; the share of time per
stage; the ten slowest and ten largest documents; OCR and AI candidates;
failed and unavailable counts; average and peak CPU and RAM of this process
and its readers (psutil, when installed).

--fresh-cache points the page cache (OCR text, box readings) at an empty
temporary folder, so the run measures the reading itself and not what an
earlier run left in the cache. The default reuses the platform's cache, as
a re-processing would.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(share * len(ordered))) - 1))]


class ResourceSampler:
    """CPU and RSS of this process and its children, sampled every second."""

    def __init__(self):
        try:
            import psutil
        except ImportError:
            psutil = None
        self.psutil = psutil
        self.cpu: list[float] = []
        self.rss: list[float] = []
        self.reader_rss: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if self.psutil is not None:
            self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self.psutil is not None:
            self._thread.join(3)
        if not self.cpu:
            return {"available": False}
        return {
            "available": True,
            "cpu_avg_pct": round(statistics.mean(self.cpu), 1), "cpu_peak_pct": round(max(self.cpu), 1),
            "rss_avg_mb": round(statistics.mean(self.rss)), "rss_peak_mb": round(max(self.rss)),
            "reader_rss_peak_mb": round(max(self.reader_rss)) if self.reader_rss else 0,
            "logical_cpus": self.psutil.cpu_count(),
        }

    def _run(self):
        psutil = self.psutil
        me = psutil.Process()
        procs = {me.pid: me}
        me.cpu_percent(None)
        while not self._stop.wait(1.0):
            total_cpu, total_rss, reader_rss = 0.0, 0.0, 0.0
            try:
                for child in me.children(recursive=True):
                    procs.setdefault(child.pid, child)
            except psutil.Error:
                pass
            for pid, proc in list(procs.items()):
                try:
                    total_cpu += proc.cpu_percent(None)
                    rss = proc.memory_info().rss / 1e6
                    total_rss += rss
                    if pid != me.pid:
                        reader_rss = max(reader_rss, rss)
                except psutil.Error:
                    procs.pop(pid, None)
            self.cpu.append(total_cpu / max(1, psutil.cpu_count()))
            self.rss.append(total_rss)
            self.reader_rss.append(reader_rss)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", required=True, help="the project folder (the synced OneDrive copy)")
    parser.add_argument("--workers", type=int, default=None, help="reader processes (default: SYNC_FILE_WORKERS)")
    parser.add_argument("--limit", type=int, default=None, help="read only the first N files of the listing")
    parser.add_argument("--fresh-cache", action="store_true", help="an empty page cache for this run")
    parser.add_argument("--no-ocr", action="store_true", help="read as if Tesseract were not installed")
    parser.add_argument("--out", help="write the summary as JSON here")
    parser.add_argument("--records", help="write every document's records here (JSON), for scripts.golden_records")
    parser.add_argument("--label", default="", help="a name for this run in the summary")
    args = parser.parse_args()

    if args.fresh_cache:
        os.environ["CACHE_ROOT"] = tempfile.mkdtemp(prefix="ep-bench-cache-")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.config import get_settings
    from app.services import document_processing, document_sync, submittal_scanner

    settings = get_settings()
    root = Path(args.folder)
    if not root.is_dir():
        print(f"not a folder: {root}", file=sys.stderr)
        return 2
    workers = args.workers if args.workers is not None else settings.sync_file_workers
    ocr = False if args.no_ocr else submittal_scanner.ocr_available()

    print(f"listing {root} ...", flush=True)
    listed = time.perf_counter()
    files = document_sync.listing(root)
    discovery_s = time.perf_counter() - listed
    if args.limit:
        files = files[: args.limit]
    plan = [(path, relative, size, mtime, None, None) for path, relative, size, mtime in files]
    print(f"{len(plan)} files in {discovery_s:.2f} s; workers={workers} ocr={ocr} "
          f"cache={'fresh' if args.fresh_cache else 'platform'}", flush=True)

    telemetry = document_sync.Telemetry()
    sampler = ResourceSampler().start()
    started = time.perf_counter()
    done = 0
    failed = unavailable = 0
    golden: dict[str, dict] = {}
    for (path, relative, size, _mtime, _sha, _row), reading in document_sync.read_in_completion_order(
            plan, ocr, workers, task=document_processing.read_task):
        write_started = time.perf_counter()
        try:
            result = reading()
        except Exception as exc:  # noqa: BLE001 -- a reader failure is one document's
            failed += 1
            telemetry.document(path=relative, seconds=time.perf_counter() - write_started, role="document",
                               result="failed", size=size)
            print(f"  FAILED {relative}: {exc}", flush=True)
            continue
        if result.get("missing"):
            failed += 1
            continue
        notes = result.get("notes") or ()
        outcome = "processed"
        if any("not downloaded" in note for note in notes):
            outcome, unavailable = "unavailable", unavailable + 1
        elif any(note.startswith("Could not read ") for note in notes):
            outcome, failed = "failed", failed + 1
        elif notes:
            outcome = "partial"
        telemetry.document(path=relative, seconds=result.get("seconds", 0.0), role=result.get("role", "document"),
                           result=outcome, size=size, timing=result.get("timing"))
        if args.records:
            from scripts.golden_records import record_key_fields

            golden[relative] = {"role": result.get("role"), "notes": list(notes),
                                "records": [record_key_fields(r) for r in (result.get("records") or ())]}
        done += 1
        if done % 25 == 0:
            elapsed = time.perf_counter() - started
            print(f"  {done}/{len(plan)} in {elapsed:.0f} s ({done / (elapsed / 60):.1f} docs/min)", flush=True)
    elapsed = time.perf_counter() - started
    resources = sampler.stop()

    summary = telemetry.result(workers=workers, planned=len(plan), discovery_seconds=round(discovery_s, 2),
                               duration_seconds=round(elapsed, 1), failed=failed, unavailable=unavailable,
                               ocr_enabled=ocr, fresh_cache=args.fresh_cache, label=args.label,
                               resources=resources, folder=str(root))
    # Every form the run classified would be read by the model in the real job.
    summary["ai_candidates"] = (summary.get("roles") or {}).get("submittal_form", 0)
    print()
    print(f"documents={summary.get('documents', 0)} duration={elapsed:.0f}s docs/min={summary.get('documents_per_minute')}")
    print(f"percentiles_ms={summary.get('percentiles_ms')}")
    print(f"stage_share_pct={summary.get('stage_share_pct')}")
    print(f"events={summary.get('events')}")
    print(f"ocr_documents={summary.get('ocr_documents')} ai_candidates={summary['ai_candidates']} "
          f"roles={summary.get('roles')} failed={failed} unavailable={unavailable}")
    print(f"resources={resources}")
    print("slowest:")
    for entry in summary.get("slowest_files", []):
        print(f"  {entry['seconds']:7.1f}s {entry['role']:15s} {entry['result']:10s} {entry['path']}")
    print("largest:")
    for entry in summary.get("largest_files", []):
        print(f"  {entry['mb']:7.1f}MB {entry['seconds']:7.1f}s {entry['role']:15s} {entry['path']}")
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
        print(f"written {args.out}")
    if args.records:
        Path(args.records).write_text(json.dumps(golden, indent=1, default=str, sort_keys=True), encoding="utf-8")
        print(f"written {args.records} ({len(golden)} documents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
