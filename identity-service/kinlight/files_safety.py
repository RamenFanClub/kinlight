"""Filename and content-type safety helpers for file uploads."""

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".doc", ".docx", ".txt", ".zip"}


def _validate_filename(filename: str) -> bool:
    """Reject filenames that look like traversal or have dangerous extensions."""
    if not filename:
        return False
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    lower = filename.lower()
    for ext in ALLOWED_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False


def _safe_filename(name: str, fallback: str = "file") -> str:
    """F119-s: sanitize a user-controlled name for use in a Content-Disposition
    header or a ZIP entry name. Strips CR/LF (header-injection) and quotes,
    collapses any path separators, and falls back if nothing safe remains."""
    if not name:
        return fallback
    cleaned = (
        name.replace("\r", "").replace("\n", "")
            .replace('"', "").replace("'", "")
            .replace("/", "_").replace("\\", "_")
            .replace("..", "_")
    ).strip()
    return cleaned or fallback


def _validate_magic_bytes(data: bytes, claimed_type: str) -> bool:
    """
    Validate that the first bytes of the file match the claimed content type.
    Uses hardcoded magic byte signatures — no external library dependency.

    TXT files are always accepted (text has no reliable magic bytes).
    """
    if len(data) < 4:
        return False  # too small to identify

    if claimed_type == "text/plain":
        return True  # text files have no reliable magic bytes

    if claimed_type == "application/pdf":
        return data[:5] == b"%PDF-"

    if claimed_type in ("image/jpeg",):
        return data[:3] == b"\xFF\xD8\xFF"

    if claimed_type in ("image/png",):
        return data[:8] == b"\x89PNG\r\n\x1a\n"

    if claimed_type in ("image/webp",):
        # WebP: RIFF header at 0–3, WEBP at 8–11
        return (data[:4] == b"RIFF" and len(data) >= 12 and
                data[8:12] == b"WEBP")

    if claimed_type in ("application/msword",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        # OLE compound (old .doc) or ZIP-based (.docx)
        return (data[:4] == b"\xD0\xCF\x11\xE0" or  # OLE2
                data[:4] == b"PK\x03\x04")            # ZIP-based Office Open XML

    if claimed_type in ("application/zip",):
        return data[:4] == b"PK\x03\x04"

    return False
