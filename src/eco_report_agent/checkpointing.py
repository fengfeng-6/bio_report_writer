from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )


def atomic_write_text(path: str | Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def checkpoint_payload(
    *,
    stage: str,
    fingerprint: str,
    value: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "stage": stage,
        "fingerprint": fingerprint,
        "value_sha256": value_sha256(value),
        "metadata": metadata or {},
        "value": value,
    }


def load_checkpoint(path: str | Path, fingerprint: str) -> Any | None:
    checkpoint_path = Path(path)
    if not checkpoint_path.is_file():
        return None
    try:
        payload = read_json(checkpoint_path)
        value = payload["value"]
        if payload.get("fingerprint") != fingerprint:
            return None
        if payload.get("value_sha256") != value_sha256(value):
            return None
        return value
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_checkpoint(
    path: str | Path,
    *,
    stage: str,
    fingerprint: str,
    value: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    atomic_write_json(
        path,
        checkpoint_payload(
            stage=stage, fingerprint=fingerprint, value=value, metadata=metadata
        ),
    )
