from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive UTC 'now', matching how SQLite round-trips DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
