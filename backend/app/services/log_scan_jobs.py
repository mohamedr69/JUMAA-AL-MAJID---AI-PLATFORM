"""Keep slow PDF/OCR scans off the request path, exposing incremental results."""
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Lock, Thread
from app.services.document_control import ControlledDocument, scan_document_control

@dataclass
class ScanState:
    records: list[ControlledDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scanning: bool = True
    processed: int = 0
    total: int = 0

_states: dict[str, ScanState] = {}
_lock = Lock()


def get_log_scan(root: Path, refresh: bool = False) -> ScanState:
    key = str(root.resolve())
    with _lock:
        state = _states.get(key)
        if state is None or (refresh and not state.scanning):
            state = ScanState()
            _states[key] = state
            Thread(target=_run, args=(root, state), daemon=True).start()
        return replace(state, records=list(state.records), warnings=list(state.warnings))


def _run(root: Path, state: ScanState):
    def progress(records, warnings, processed, total):
        with _lock:
            state.records = records
            state.warnings = warnings
            state.processed = processed
            state.total = total
    try:
        records, warnings = scan_document_control(root, progress=progress)
        with _lock:
            state.records, state.warnings = records, warnings
    except Exception as exc:
        with _lock:
            state.warnings.append(f"Directory scan could not finish: {exc}")
    finally:
        with _lock:
            state.scanning = False
