"""Structured logging helpers: PII masking and a JSON line formatter."""

import json
import logging
import re as re_mod

_EMAIL_RE = re_mod.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def mask_email(value: str) -> str:
    """Mask email addresses in a string: alice@example.com -> a***@example.com"""
    def _mask(m: re_mod.Match) -> str:
        email = m.group(0)
        local, domain = email.split("@", 1)
        if len(local) <= 1:
            return f"{local}***@{domain}"
        return f"{local[0]}***@{domain}"
    return _EMAIL_RE.sub(_mask, value)


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with PII masking."""
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        masked = mask_email(msg)
        entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": masked,
        }
        return json.dumps(entry)
