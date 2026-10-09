from __future__ import annotations

import json
import fcntl
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from .checkpointing import load_checkpoint, save_checkpoint, value_sha256


class FatalLLMError(RuntimeError):
    """Authentication or configuration failure that must pause the batch."""


def _resolve_api_key(api_key_env: str) -> tuple[str, str]:
    """Resolve an explicit custom name, then the two supported writer names.

    The default ECO name is kept for backwards compatibility, but the public
    REPORT name wins when no non-default CLI override was supplied.
    """
    names: list[str] = []
    if api_key_env and api_key_env != "ECO_REPORT_WRITER_API_KEY":
        names.append(api_key_env)
    names.extend(["REPORT_WRITER_API_KEY", "ECO_REPORT_WRITER_API_KEY"])
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return api_key_env or "REPORT_WRITER_API_KEY", ""


def _verified_ssl_context() -> ssl.SSLContext:
    """Build a verified TLS context on compute nodes without a default CA file."""
    configured = os.environ.get("SSL_CERT_FILE", "").strip()
    if configured:
        return ssl.create_default_context(cafile=configured)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        pass
    for candidate in (
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/certs/ca-certificates.crt",
    ):
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def _checkpoint_path(directory: Path, label: str, fingerprint: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "request"
    return directory / f"{safe}-{fingerprint[:16]}.json"


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None



def _acquire_global_rate_slot() -> None:
    raw_limit = os.environ.get("ECO_REPORT_REQUESTS_PER_MINUTE", "").strip()
    state_name = os.environ.get("ECO_REPORT_RATE_STATE", "").strip()
    if not raw_limit or not state_name:
        return
    limit = int(raw_limit)
    if limit < 1:
        raise ValueError("ECO_REPORT_REQUESTS_PER_MINUTE must be positive")
    state = Path(state_name)
    state.parent.mkdir(parents=True, exist_ok=True)
    while True:
        now = time.time()
        with state.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                timestamps = [float(value) for value in json.load(handle)]
            except (json.JSONDecodeError, TypeError, ValueError):
                timestamps = []
            timestamps = sorted(value for value in timestamps if value > now - 60.0)
            if len(timestamps) < limit:
                timestamps.append(now)
                handle.seek(0)
                handle.truncate()
                json.dump(timestamps, handle)
                handle.flush()
                os.fsync(handle.fileno())
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return
            delay = max(0.05, timestamps[0] + 60.0 - now)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        time.sleep(min(delay, 60.0))


def request_chat_json(
    *,
    base_url: str,
    model: str,
    api_key_env: str,
    timeout_seconds: int,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, str]],
    label: str,
    checkpoint_directory: Path | None,
    max_attempts: int = 4,
    cache_validator: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any], int, list[float], bool]:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    fingerprint = value_sha256(body)
    checkpoint = (
        _checkpoint_path(Path(checkpoint_directory), label, fingerprint)
        if checkpoint_directory is not None
        else None
    )
    if checkpoint is not None:
        cached = load_checkpoint(checkpoint, fingerprint)
        if isinstance(cached, dict) and (
            cache_validator is None or bool(cache_validator(cached))
        ):
            return cached, 0, [], True
    _, api_key = _resolve_api_key(api_key_env)
    if not api_key:
        raise FatalLLMError(
            "missing API key environment variable: "
            + ", ".join(dict.fromkeys(filter(None, [api_key_env, "REPORT_WRITER_API_KEY", "ECO_REPORT_WRITER_API_KEY"])))
        )

    durations: list[float] = []
    last_error: Exception | None = None
    ssl_context = _verified_ssl_context()
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        _acquire_global_rate_slot()
        started = time.perf_counter()
        retry_after: float | None = None
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds, context=ssl_context
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            result = content if isinstance(content, dict) else json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("LLM response is not a JSON object")
            cacheable = cache_validator is None or bool(cache_validator(result))
            if checkpoint is not None and cacheable:
                save_checkpoint(
                    checkpoint,
                    stage=f"llm:{label}",
                    fingerprint=fingerprint,
                    value=result,
                    metadata={"model": model, "label": label},
                )
            return result, attempt, durations, False
        except urllib.error.HTTPError as exc:
            # Provider response bodies may echo token identifiers or request data.
            # Persist only the HTTP code; retry timing comes from the header below.
            if exc.code in {401, 403}:
                raise FatalLLMError(f"LLM API HTTP {exc.code}") from exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                last_error = RuntimeError(f"LLM API HTTP {exc.code}")
            else:
                raise RuntimeError(f"LLM API HTTP {exc.code}") from exc
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise RuntimeError(
                f"invalid LLM JSON response: {type(exc).__name__}:{exc}"
            ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
        finally:
            durations.append(round(time.perf_counter() - started, 3))
        if attempt >= max_attempts:
            break
        delays = (5.0, 15.0, 45.0)
        delay = retry_after if retry_after is not None else delays[min(attempt - 1, 2)]
        time.sleep(max(0.0, delay) + random.uniform(0.0, 1.5))
    raise RuntimeError(
        f"LLM request failed after {max_attempts} attempts: "
        f"{type(last_error).__name__}:{last_error}"
    ) from last_error
