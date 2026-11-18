from datetime import UTC, datetime


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a datetime to timezone-aware UTC (SQLite returns naive values)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
