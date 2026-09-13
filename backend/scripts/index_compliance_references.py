"""Index the company's past compliance statements from the project archive.

The platform does this in the background from the Compliance Statement page;
this runs the same walk from a terminal, with progress, which is the easier
way to do the first (long) one.

    cd backend
    .\\venv\\Scripts\\python scripts\\index_compliance_references.py
    .\\venv\\Scripts\\python scripts\\index_compliance_references.py --full

Only files are read; nothing in the archive is written.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from app.compliance import references  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="Re-read every statement, not only changed ones.")
    args = parser.parse_args(argv)

    done = threading.Event()

    def report() -> None:
        while not done.wait(15):
            s = references.status()
            print(f"  seen {s['files_seen']}  read {s['files_read']}  errors {s['errors']}  indexed {s['indexed']}", flush=True)

    threading.Thread(target=report, daemon=True).start()
    started = time.time()
    result = references.scan(full=args.full)
    done.set()
    print(f"done in {time.time() - started:.0f}s: {result['indexed']} statements, by system {result['by_system']}, "
          f"{result['errors']} unreadable" + (f" -- {result['message']}" if result.get("message") else ""))
    return 0 if not result.get("message") else 1


if __name__ == "__main__":
    raise SystemExit(main())
