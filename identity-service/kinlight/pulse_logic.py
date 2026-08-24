"""Pure pulse-domain logic: overdue detection, reminders, and warnings.

These helpers read only the vault document and free-standing constants — no
database, encryption, or email side effects — so the dead-man's-switch timing
rules stay isolated and easy to reason about.
"""

from datetime import timedelta

from kinlight.timeutil import ensure_utc, now_utc

# F64-2: Warning days for the ping_then_notify protocol.
# Warnings fire on these days of overdue; contacts are notified on day 3+.
WARNING_DAYS = [1, 2]
CONTACT_NOTIFY_AFTER_DAYS = 3


def _interval_days(vault_doc: dict) -> int:
    """Check-in interval expressed in days (months approximated at 30 days)."""
    freq = vault_doc.get("checkInFrequency", 2)
    unit = vault_doc.get("checkInUnit", "months")
    return freq * 30 if unit == "months" else freq * 7


def is_overdue(vault_doc: dict) -> tuple[bool, int]:
    """
    Returns (is_overdue, days_overdue).
    Grace period starts after the check-in window expires.
    """
    last_checkin = vault_doc.get("lastCheckin")
    if not last_checkin:
        return False, 0

    last_checkin = ensure_utc(last_checkin)
    grace_days = vault_doc.get("gracePeriodDays", 7)
    grace_end = last_checkin + timedelta(days=_interval_days(vault_doc) + grace_days)
    now = now_utc()

    if now > grace_end:
        return True, (now - grace_end).days
    return False, 0


def is_reminder_due(vault_doc: dict) -> bool:
    """
    Returns True when the vault holder should receive a check-in reminder.

    Mirrors the frontend 25% rule: fires when time remaining <= 25% of the
    interval, but only once per cycle (guarded by the reminderSent flag).
    Does NOT fire if the vault is already overdue.
    """
    last_checkin = vault_doc.get("lastCheckin")
    if not last_checkin or vault_doc.get("reminderSent", False):
        return False

    last_checkin = ensure_utc(last_checkin)
    interval = _interval_days(vault_doc)
    threshold = max(7, round(interval * 0.25))
    days_remaining = (last_checkin + timedelta(days=interval) - now_utc()).days

    return 0 <= days_remaining <= threshold


def should_send_warning(vault_doc: dict, days_overdue: int) -> bool:
    """
    Returns True if a warning email should be sent to the holder today.
    Only applies to ping_then_notify protocol, and only on WARNING_DAYS (1, 2).
    Guards against re-sending on the same day via warningSentDays list.
    """
    if vault_doc.get("notifyProto", "ping_then_notify") != "ping_then_notify":
        return False
    if days_overdue not in WARNING_DAYS:
        return False
    already_sent = vault_doc.get("warningSentDays", [])
    return days_overdue not in already_sent


def should_notify_contacts(vault_doc: dict, days_overdue: int) -> bool:
    """
    For ping_then_notify: contacts notified only after CONTACT_NOTIFY_AFTER_DAYS (3).
    For all other protocols: existing behaviour (notify immediately / escalate).
    """
    proto = vault_doc.get("notifyProto", "ping_then_notify")
    if proto == "ping_then_notify":
        return days_overdue >= CONTACT_NOTIFY_AFTER_DAYS
    return True
