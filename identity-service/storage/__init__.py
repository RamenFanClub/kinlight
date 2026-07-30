"""
Storage backend factory.

Usage:
    from storage import get_storage_backend
    storage = get_storage_backend(db)

    file_id = storage.upload(data, "will.pdf", "application/pdf",
                             {"userId": "abc123"})
    data, name, ct = storage.download(file_id)
    storage.delete(file_id)

To swap backends set STORAGE_BACKEND env var (default "gridfs").
"""

import os

from .base import StorageBackend
from .gridfs_backend import GridFSBackend

__all__ = ["StorageBackend", "get_storage_backend"]


def get_storage_backend(db) -> StorageBackend:
    """Return the active storage backend based on config."""
    backend_name = os.environ.get("STORAGE_BACKEND", "gridfs").lower()

    if backend_name == "gridfs":
        if db is None:
            raise RuntimeError("MongoDB connection required for GridFS storage backend.")
        return GridFSBackend(db)

    # Future: elif backend_name == "s3": return S3Backend(...)
    raise ValueError(f"Unknown storage backend: {backend_name}")
