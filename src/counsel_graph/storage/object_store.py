"""MinIO-backed object storage (S3-compatible, self-hosted, Supabase-Storage-
compatible wire protocol) for uploaded documents. Replaces local-disk
UPLOAD_DIR.write_bytes() in api/main.py's upload handler: uploaded bytes go to
the object store and only the returned storage_key is persisted on the
document row.

Env vars: STORAGE_ENDPOINT, STORAGE_ACCESS_KEY, STORAGE_SECRET_KEY, STORAGE_BUCKET.
Uses boto3's S3 client against the MinIO endpoint rather than the `minio` package,
since boto3 is the more commonly already-installed dependency and MinIO's S3 API
is a drop-in target for it.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

_client = None


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Add it to your .env file (see .env.example) or export it directly."
        )
    return value


def get_client():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=_require("STORAGE_ENDPOINT"),
            aws_access_key_id=_require("STORAGE_ACCESS_KEY"),
            aws_secret_access_key=_require("STORAGE_SECRET_KEY"),
        )
    return _client


def _bucket() -> str:
    return _require("STORAGE_BUCKET")


def ensure_bucket() -> None:
    """Idempotent: creates the configured bucket if it doesn't already exist."""
    client = get_client()
    bucket = _bucket()
    from botocore.exceptions import ClientError
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def put_object(data: bytes, filename: str) -> str:
    """Uploads raw bytes, returns the storage_key to persist on the document row.
    The key is namespaced with a random prefix so two uploads of the same filename
    never collide, mirroring the old UPLOAD_DIR uuid-prefixed local filenames."""
    storage_key = f"{uuid.uuid4().hex}/{filename}"
    get_client().put_object(Bucket=_bucket(), Key=storage_key, Body=data)
    return storage_key


def get_object_bytes(storage_key: str) -> bytes:
    response = get_client().get_object(Bucket=_bucket(), Key=storage_key)
    return response["Body"].read()


def delete_object(storage_key: str) -> None:
    get_client().delete_object(Bucket=_bucket(), Key=storage_key)


@contextmanager
def fetch_to_temp_file(storage_key: str, suffix: str = "") -> Iterator[str]:
    """Downloads an object to a temp file for processing (OCR, pdfplumber extraction
    both need a real file path, not a byte stream), yields the path, and always
    cleans the temp file up afterward regardless of what the caller does with it."""
    data = get_object_bytes(storage_key)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def is_configured() -> bool:
    """Whether MinIO/object-store env vars are set at all, so callers (e.g. the API
    startup) can decide whether to fall back to local disk instead of hard failing."""
    return all(os.getenv(v) for v in ("STORAGE_ENDPOINT", "STORAGE_ACCESS_KEY", "STORAGE_SECRET_KEY", "STORAGE_BUCKET"))
