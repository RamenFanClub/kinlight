"""Pure timestamp helpers shared across the Kinlight backend."""

from datetime import datetime, timezone
from typing import Optional


def ms_to_dt(ms: Optional[int]) -> Optional[datetime]:
    """Convert JS millisecond timestamp to Python datetime (UTC)."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert Python datetime to JS millisecond timestamp."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_utc() -> datetime:
    """Current time, normalized to UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone if naive."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
