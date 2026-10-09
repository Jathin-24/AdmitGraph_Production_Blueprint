"""P2-15 — on-disk storage for uploaded application-document files.

Layout: ``{settings.upload_dir}/{document_id}/{uuid4}.{ext}`` (defaults to
``backend/var/uploads``). The document id directory makes cleanup on document
delete trivial and the random file name means a stored path can never be
predicted or traversed — user-supplied names are only kept as
``Document.file_name`` metadata, never as a path.

Rules enforced here (mirroring the frontend's ``uploadRejection``):

* extension allowlist: pdf / png / jpg / jpeg / docx;
* content type must be sane (the declared type of an allowed extension, or
  the generic ``application/octet-stream`` some browsers send);
* maximum 10 MB (``MAX_UPLOAD_BYTES``) — larger bodies are cut off while
  reading, before anything is written to disk.

Everything rejects with the API's standard envelope (``AppError``), so the
router stays thin.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import AppError

# Keep in sync with frontend/app/lib/api-extra.ts MAX_UPLOAD_BYTES.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# extension -> the only content type we accept for it.
ALLOWED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Some clients (and the raw FormData path) send no useful type at all.
_GENERIC_TYPES = {"", "application/octet-stream"}

UPLOAD_CHUNK = 1024 * 1024

TOO_LARGE_MESSAGE = "That file is too large — the limit is 10 MB."
BAD_TYPE_MESSAGE = "Unsupported file type — use PDF, PNG, JPG or DOCX."
EMPTY_MESSAGE = "That file is empty — choose a file with content."
NO_FILE_MESSAGE = "A file is required"


def upload_root() -> Path:
    """Absolute upload directory (created lazily by :func:`store_bytes`)."""
    return Path(get_settings().upload_dir)


def validate(filename: str | None, content_type: str | None) -> str:
    """Check the declared name/type; returns the normalized extension.

    Raises 422 VALIDATION_ERROR for anything not on the allowlist.
    """
    name = (filename or "").strip()
    if not name:
        raise AppError(422, "VALIDATION_ERROR", NO_FILE_MESSAGE)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_TYPES:
        raise AppError(422, "VALIDATION_ERROR", BAD_TYPE_MESSAGE)
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in _GENERIC_TYPES and declared != ALLOWED_TYPES[ext]:
        raise AppError(422, "VALIDATION_ERROR", BAD_TYPE_MESSAGE)
    return ext


def ensure_size_ok(size: int) -> None:
    if size <= 0:
        raise AppError(422, "VALIDATION_ERROR", EMPTY_MESSAGE)
    if size > MAX_UPLOAD_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", TOO_LARGE_MESSAGE)


async def read_upload(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an ``UploadFile`` fully, rejecting oversize bodies while reading.

    Stops as soon as the limit is crossed so a huge upload never sits in
    memory (the middleware body cap is a second, larger net).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(413, "FILE_TOO_LARGE", TOO_LARGE_MESSAGE)
        chunks.append(chunk)
    if total == 0:
        raise AppError(422, "VALIDATION_ERROR", EMPTY_MESSAGE)
    return b"".join(chunks)


def store_bytes(document_id: uuid.UUID, data: bytes, ext: str) -> str:
    """Persist bytes under the document's own directory.

    Returns the path *relative to the upload root* — that string is what the
    row stores, so the root can move between environments.
    """
    rel = f"{document_id}/{uuid.uuid4().hex}{ext}"
    target = upload_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


def resolve_stored(relative: str) -> Path:
    """Absolute path for a stored row, refusing anything outside the root."""
    root = upload_root().resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise AppError(404, "NOT_FOUND", "File not found")
    return target


def delete_stored(relative: str | None) -> bool:
    """Remove a stored file if present; True when something was deleted."""
    if not relative:
        return False
    path = resolve_stored(relative)
    if not path.is_file():
        return False
    path.unlink()
    try:  # tidy the per-document directory once it is empty
        path.parent.rmdir()
    except OSError:  # not empty / already gone — irrelevant
        pass
    return True
