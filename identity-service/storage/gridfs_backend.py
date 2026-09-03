"""
GridFS storage backend — stores files in MongoDB GridFS (bundled with pymongo).

Files are split into 255 KB chunks across fs.files and fs.chunks collections
in the existing Atlas cluster. No new infrastructure needed.

Encryption is handled OUTSIDE this backend (in main.py) so the storage layer
remains pure storage — swap to S3 later without touching crypto.
"""

from typing import Optional, Tuple

from bson import ObjectId
from gridfs import GridFS, NoFile

from .base import StorageBackend


class GridFSBackend(StorageBackend):
    """Stores files in GridFS using the existing MongoDB connection."""

    def __init__(self, db):
        self.fs = GridFS(db)

    # ── public API ──────────────────────────────────────────────────────────

    def upload(self, file_data: bytes, filename: str,
               content_type: str, metadata: dict) -> str:
        file_id = self.fs.put(
            file_data,
            filename=filename,
            contentType=content_type,
            metadata=metadata,
        )
        return str(file_id)

    def download(self, file_id: str) -> Optional[Tuple[bytes, str, str, bool]]:
        try:
            oid = ObjectId(file_id)
        except Exception:
            return None
        try:
            grid_out = self.fs.get(oid)
            data = grid_out.read()
            meta = grid_out.metadata or {}
            filename_encrypted = bool(meta.get("filenameEncrypted"))
            filename = grid_out.filename or meta.get("filename", "file")
            return data, filename, grid_out.content_type, filename_encrypted
        except (NoFile, Exception):
            return None

    def delete(self, file_id: str) -> bool:
        try:
            oid = ObjectId(file_id)
        except Exception:
            return False
        try:
            self.fs.delete(oid)
            return True
        except (NoFile, Exception):
            return False

    def get_owner(self, file_id: str) -> Optional[str]:
        """Return the userId stored in metadata, or None if file not found."""
        try:
            oid = ObjectId(file_id)
        except Exception:
            return None
        try:
            grid_out = self.fs.get(oid)
            meta = grid_out.metadata or {}
            return meta.get("userId")
        except (NoFile, Exception):
            return None
