from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


DIFY_TARGET_VERSION = "1.16.1"
DIFY_DSL_VERSION = "0.7.0"
API_BASE_PLACEHOLDER = "__ECO_REPORT_API_BASE_URL__"


class DifyDslError(ValueError):
    pass


def validate_api_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DifyDslError("API base URL must be an absolute http(s) URL")
    return normalized


def validate_dsl_text(content: str) -> list[str]:
    issues: list[str] = []
    required_fragments = {
        "kind": "kind: app",
        "version": f"version: {DIFY_DSL_VERSION}",
        "workflow_mode": "mode: workflow",
        "start_node": "type: start",
        "http_node": "type: http-request",
        "end_node": "type: end",
        "report_endpoint": "/v1/report-markdown",
        "dependencies": "dependencies: []",
    }
    for name, fragment in required_fragments.items():
        if fragment not in content:
            issues.append(f"missing:{name}")
    if API_BASE_PLACEHOLDER in content:
        issues.append("unresolved:api_base_url")
    if content.count("type: start") != 1:
        issues.append("graph:start_node_count")
    if content.count("type: end") != 1:
        issues.append("graph:end_node_count")
    if "sourceType: start" not in content or "targetType: http-request" not in content:
        issues.append("graph:start_to_http_edge")
    if "sourceType: http-request" not in content or "targetType: end" not in content:
        issues.append("graph:http_to_end_edge")
    return issues


def render_dsl(template_path: str | Path, api_base_url: str) -> str:
    template = Path(template_path).read_text(encoding="utf-8")
    if template.count(API_BASE_PLACEHOLDER) != 1:
        raise DifyDslError("DSL template must contain exactly one API base URL placeholder")
    content = template.replace(API_BASE_PLACEHOLDER, validate_api_base_url(api_base_url))
    issues = validate_dsl_text(content)
    if issues:
        raise DifyDslError("Invalid generated DSL: " + ", ".join(issues))
    return content
