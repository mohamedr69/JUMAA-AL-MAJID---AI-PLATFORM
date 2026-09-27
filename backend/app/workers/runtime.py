"""What a long-lived backend process is running: its fingerprint, and the
check that its runtime is whole before it takes a job.

The workers (app.workers.sync_worker, document_worker, ifc_worker) have
no hot reload: `app.models` is loaded once, when the process starts, and
the service modules are imported lazily, per job. A code change after
the start then mixes new service code with the old `app.models` already
in memory -- and a symbol the new code imports from it is not there. On
2026-09-27 that was `DocumentClassification`: the document worker
started at 12:16, the class was added to models.py at 14:23, and the
first processing job after that failed with "cannot import name
'DocumentClassification' from 'app.models'" while every file on disk
was right. A worker that had run for hours failed on a job, not at start.

So every worker now says at start what it is running (`announce`), and
proves it can import what its jobs will need (`validate`) -- failing at
once, with the paths in the message, rather than hours later. A worker
started under the wrong interpreter, from the wrong checkout, or before
a code change reads as such in its first lines of log.

    venv\\Scripts\\python -m app.workers.runtime document-worker    (the same check, by hand)
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

log = logging.getLogger("app.workers.runtime")

# What every worker's jobs import from app.models one way or another.
REQUIRED_MODELS = ("Project", "ProjectDocument", "BackgroundJob", "BackgroundWorker", "DocumentReading")
# With Document Classification V2 on, what the processing imports as well.
CLASSIFICATION_MODELS = ("DocumentClassification",)
# The service modules each worker runs, imported at start rather than on
# the first job, so a module that cannot be imported says so at once.
SERVICES = {
    "sync-worker": ("app.services.sync_service", "app.services.document_sync"),
    "document-worker": ("app.services.document_processing", "app.services.document_sync"),
    "ifc-worker": ("app.ifc.services.runners",),
    "api": (),
}


class RuntimeMismatch(RuntimeError):
    """The process cannot run its jobs as it is: the message says what is
    missing and where the process loaded its code from."""


def root() -> Path:
    """The backend folder this process imported `app` from."""
    import app

    return Path(app.__file__).resolve().parent.parent


def _git_revision(backend: Path) -> str | None:
    """The checked-out revision, read off the repository's own files (no
    git needed, and none required): None when it cannot be told."""
    try:
        git = backend.parent / ".git"
        if git.is_file():   # a submodule: "gitdir: <path>"
            text = git.read_text(encoding="utf-8").strip()
            git = (backend.parent / text.split(":", 1)[1].strip()).resolve() if text.startswith("gitdir:") else None
        if git is None or not git.is_dir():
            return None
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = git / head.split(":", 1)[1].strip()
            head = ref.read_text(encoding="utf-8").strip() if ref.is_file() else head
        return head[:12] or None
    except OSError:
        return None


def fingerprint(process: str, models: ModuleType | None = None) -> dict:
    """Where this process runs from: nothing secret, everything a mismatch
    would show in."""
    from app.core.config import get_settings

    if models is None:
        import app.models as models   # noqa: PLC0415 -- the module as this process loaded it
    backend = root()
    settings = get_settings()
    out = {
        "process": process, "pid": os.getpid(), "python": sys.executable,
        "python_version": platform.python_version(), "cwd": os.getcwd(), "root": str(backend),
        "models": str(Path(getattr(models, "__file__", "?")).resolve()) if getattr(models, "__file__", None) else "?",
        "revision": _git_revision(backend), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classification_v2": bool(getattr(settings, "document_classification_v2", False)),
        "classification_rules": None,
    }
    if out["classification_v2"]:
        try:
            from app.services import document_classification

            out["classification_rules"] = document_classification.RULES_VERSION
        except Exception as exc:  # noqa: BLE001 -- what `validate` reports properly
            out["classification_rules"] = f"unavailable ({type(exc).__name__}: {exc})"
    return out


def describe(fp: dict) -> str:
    lines = [f"{fp['process']} started", f"  PID: {fp['pid']}", f"  Python: {fp['python']} ({fp['python_version']})",
             f"  Root: {fp['root']}", f"  Models: {fp['models']}", f"  CWD: {fp['cwd']}",
             f"  Revision: {fp['revision'] or 'unknown'}"]
    if fp["classification_v2"]:
        lines.append(f"  Classification V2: on, rules {fp['classification_rules']}")
    else:
        lines.append("  Classification V2: off")
    return "\n".join(lines)


def announce(process: str) -> dict:
    """Log the fingerprint (one concise block) and return it."""
    fp = fingerprint(process)
    log.info("%s", describe(fp))
    return fp


def validate(process: str, *, models: ModuleType | None = None, classification: bool | None = None,
             import_services: bool = True) -> dict:
    """Prove the runtime is whole, or raise `RuntimeMismatch` saying what is
    wrong and where the code came from. Checks: `app.models` was loaded
    from this process's own backend folder; the models the jobs need are
    on that module (`DocumentClassification` too when the feature is on);
    the worker's service modules import; and, as a warning only, the
    interpreter is the backend's own venv."""
    from app.core.config import get_settings

    if models is None:
        import app.models as models   # noqa: PLC0415
    fp = fingerprint(process, models)
    backend = Path(fp["root"])
    problems: list[str] = []
    models_path = Path(fp["models"]) if fp["models"] != "?" else None
    if models_path is None or not str(models_path).lower().startswith(str(backend).lower()):
        problems.append(f"app.models was loaded from {fp['models']}, not from this backend ({backend})")
    if classification is None:
        classification = bool(getattr(get_settings(), "document_classification_v2", False))
    required = REQUIRED_MODELS + (CLASSIFICATION_MODELS if classification else ())
    missing = [name for name in required if not hasattr(models, name)]
    if missing:
        problems.append(f"required model{'s' if len(missing) != 1 else ''} {', '.join(missing)} unavailable on the loaded app.models"
                        " (the process started before the code that defines it, or runs another checkout)")
    if import_services and not problems:
        for name in SERVICES.get(process, ()) + (("app.services.document_classification",) if classification else ()):
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 -- reported, with the paths, below
                problems.append(f"{name} cannot be imported: {type(exc).__name__}: {exc}")
    venv = backend / "venv"
    if venv.is_dir() and not str(Path(sys.executable).resolve()).lower().startswith(str(venv.resolve()).lower()):
        log.warning("%s runs under %s, not the backend's venv (%s)", process, sys.executable, venv)
    if problems:
        raise RuntimeMismatch(
            f"{process} startup failed:\n  " + "\n  ".join(problems)
            + f"\nLoaded models from: {fp['models']}\nPython: {fp['python']}\nRoot: {fp['root']}\nProcess: {process} (PID {fp['pid']})"
            "\nRestart the process from the backend folder with its venv after every backend code change (start-backend.bat)."
        )
    return fp


def start(process: str) -> dict:
    """What a worker's `main` calls first: announce, then validate; a
    mismatch is logged and ends the process with exit status 2."""
    fp = announce(process)
    try:
        validate(process)
    except RuntimeMismatch as exc:
        log.error("%s", exc)
        raise SystemExit(2) from exc
    log.info("%s runtime check passed", process)
    return fp


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    process = (argv or sys.argv[1:] or ["api"])[0]
    try:
        fp = validate(process)
    except RuntimeMismatch as exc:
        print(exc)
        return 2
    print(describe(fp))
    print(f"{process} runtime check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
