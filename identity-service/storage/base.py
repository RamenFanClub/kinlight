"""
Abstract storage backend interface.

Add new backends by subclassing StorageBackend. The factory in __init__.py
selects the active backend via STORAGE_BACKEND env var (default: "gridfs").

To add S3 later:
  1. Create storage/s3_backend.py with an S3Backend(StorageBackend)
  2. Set STORAGE_BACKEND=s3 + S3_BUCKET / S3_REGION / AWS creds
  3. No API or frontend changes needed
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class StorageBackend(ABC):
    """
    Pluggable file storage. Every implementation must handle:
      - upload   → store bytes, return an opaque string id
      - download → retrieve bytes + metadata by id
      - delete   → remove the file, return True on success
      - get_owner → return the userId that owns the file, or None
    """

    @abstractmethod
    def upload(self, file_data: bytes, filename: str,
               content_type: str, metadata: dict) -> str:
        """Store file_data. Return an opaque file_id string."""
        ...

    @abstractmethod
    def download(self, file_id: str) -> Optional[Tuple[bytes, str, str, bool]]:
        """
        Retrieve (data, filename, content_type, filename_is_encrypted) for a file_id.
        filename_is_encrypted is True when the caller stored an encrypted filename
        (F132) and must decrypt before use. Return None if the file does not exist.
        """
        ...

    @abstractmethod
    def delete(self, file_id: str) -> bool:
        """Remove a file by id. Return True if deleted, False if not found."""
        ...

    @abstractmethod
    def get_owner(self, file_id: str) -> Optional[str]:
        """Return the userId string stored with the file, or None."""
        ...
