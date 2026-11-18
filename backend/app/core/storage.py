"""Object storage abstraction for evidence and generated reports.

LocalStorage is write-once: objects are created exclusively (never overwritten) and made read-only.
S3Storage implements the same interface for S3-compatible stores (requires `boto3`; not exercised by the
test-suite - see docs/ARCHITECTURE.md).
"""
import hashlib
import os
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings

CHUNK = 1024 * 1024


class StorageError(Exception):
    pass


class ObjectExists(StorageError):
    pass


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, stream: BinaryIO, max_bytes: int | None = None) -> tuple[str, int]:
        """Store a new object. Returns (sha256, size). Must refuse to overwrite an existing key."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def sha256(self, key: str) -> str:
        h = hashlib.sha256()
        with self.open(key) as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()


class TooLarge(StorageError):
    pass


def _safe_key(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise StorageError("empty storage key")
    return "/".join(parts)


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    def put(self, key, stream, max_bytes=None):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        try:
            f = open(path, "xb")  # exclusive create: never overwrite
        except FileExistsError as e:
            raise ObjectExists(key) from e
        try:
            with f:
                for chunk in iter(lambda: stream.read(CHUNK), b""):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise TooLarge(f"object exceeds {max_bytes} bytes")
                    h.update(chunk)
                    f.write(chunk)
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP)  # read-only original
        except Exception:
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
            path.unlink(missing_ok=True)
            raise
        return h.hexdigest(), size

    def open(self, key):
        return open(self._path(key), "rb")

    def exists(self, key):
        return self._path(key).exists()


class S3Storage(Storage):  # pragma: no cover - requires an S3 endpoint
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str | None = None):
        import boto3  # optional dependency
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url)
        self.bucket, self.prefix = bucket, prefix

    def put(self, key, stream, max_bytes=None):
        import tempfile
        h, size = hashlib.sha256(), 0
        with tempfile.TemporaryFile() as tmp:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise TooLarge(f"object exceeds {max_bytes} bytes")
                h.update(chunk)
                tmp.write(chunk)
            tmp.seek(0)
            full = self.prefix + _safe_key(key)
            if self.exists(key):
                raise ObjectExists(key)
            self.s3.upload_fileobj(tmp, self.bucket, full, ExtraArgs={"ChecksumAlgorithm": "SHA256"})
        return h.hexdigest(), size

    def open(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=self.prefix + _safe_key(key))["Body"]

    def exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self.prefix + _safe_key(key))
            return True
        except Exception:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            if not s.s3_bucket:
                raise RuntimeError("FL_S3_BUCKET is required for the s3 storage backend")
            _storage = S3Storage(s.s3_bucket, s.s3_prefix, s.s3_endpoint_url)
        else:
            _storage = LocalStorage(s.storage_dir)
    return _storage


def set_storage(storage: Storage | None) -> None:
    global _storage
    _storage = storage
