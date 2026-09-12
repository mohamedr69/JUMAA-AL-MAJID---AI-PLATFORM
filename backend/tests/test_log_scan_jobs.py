from threading import Event
from app.services import log_scan_jobs as jobs


def test_scan_returns_progress_without_duplicate_refresh(tmp_path, monkeypatch):
    release = Event()
    started = Event()
    calls = []
    def scan(root, progress):
        calls.append(root)
        progress([], ["Checking documents"], 1, 2)
        started.set()
        release.wait(3)
        return [], []
    monkeypatch.setattr(jobs, "scan_document_control", scan)
    first = jobs.get_log_scan(tmp_path)
    assert first.scanning
    try:
        assert started.wait(2)
        state = jobs.get_log_scan(tmp_path, refresh=True)
        assert (state.processed, state.total) == (1, 2)
        assert state.warnings == ["Checking documents"]
        assert len(calls) == 1
    finally:
        release.set()
