from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .dify_dsl import DifyDslError, validate_dsl_text


@dataclass(frozen=True)
class ImportResult:
    status_code: int
    body: dict[str, Any]


def build_console_auth_headers(access_token: str, csrf_token: str) -> dict[str, str]:
    if not access_token.strip():
        raise ValueError("A non-empty Dify Console access token is required")
    if not csrf_token.strip():
        raise ValueError("A non-empty Dify Console CSRF token is required")
    return {
        "Authorization": f"Bearer {access_token}",
        "X-CSRF-Token": csrf_token,
        "Cookie": f"csrf_token={csrf_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_import_payload(dsl_text: str, app_id: str | None = None) -> dict[str, Any]:
    issues = validate_dsl_text(dsl_text)
    if issues:
        raise DifyDslError("Refusing to deploy invalid DSL: " + ", ".join(issues))
    payload: dict[str, Any] = {"mode": "yaml-content", "yaml_content": dsl_text}
    if app_id:
        payload["app_id"] = app_id
    return payload


def import_workflow(
    console_base_url: str,
    access_token: str,
    dsl_text: str,
    app_id: str | None = None,
    timeout_seconds: int = 30,
    *,
    csrf_token: str,
) -> ImportResult:
    endpoint = urljoin(console_base_url.rstrip("/") + "/", "console/api/apps/imports")
    payload = json.dumps(build_import_payload(dsl_text, app_id), ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        method="POST",
        headers=build_console_auth_headers(access_token, csrf_token),
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
            return ImportResult(status_code=response.status, body=body)
    except HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(response_text)
        except json.JSONDecodeError:
            body = {"error": "non_json_response", "status_code": exc.code}
        return ImportResult(status_code=exc.code, body=body)
