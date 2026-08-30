from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .docx_report import build_docx_report
from .ingest import InputValidationError
from .models import ReportIR
from .pdf_report import write_pdf_report
from .pipeline import build_report_ir
from .markdown_report import render_markdown_report
from .report_plan import build_report_package


class RendererUnavailableError(RuntimeError):
    pass


def _single(query: dict[str, list[str]], name: str, required: bool = True) -> str | None:
    values = query.get(name, [])
    if not values or not values[0].strip():
        if required:
            raise InputValidationError(f"Missing query parameter: {name}")
        return None
    return values[0].strip()


def _report_from_query(
    workbook_path: str | Path,
    query: dict[str, list[str]],
) -> ReportIR:
    project_type = _single(query, "project_type")
    fid_raw = _single(query, "fid")
    year_raw = _single(query, "target_year", required=False)
    project_name = _single(query, "project_name", required=False)
    try:
        fid = int(fid_raw or "")
        target_year = None if year_raw is None else int(year_raw)
    except ValueError as exc:
        raise InputValidationError("fid and target_year must be integers") from exc
    return build_report_ir(workbook_path, project_type or "", fid, target_year, project_name)


def build_report_response(
    workbook_path: str | Path,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    return _report_from_query(workbook_path, query).to_dict()


def build_report_package_response(
    workbook_path: str | Path,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    return build_report_package(_report_from_query(workbook_path, query))


def renderer_capabilities(enable_pdf_renderer: bool = False) -> dict[str, dict[str, Any]]:
    xelatex_available = shutil.which("xelatex") is not None
    return {
        "docx": {
            "available": True,
            "backend": "stdlib-ooxml",
        },
        "pdf": {
            "available": enable_pdf_renderer and xelatex_available,
            "enabled": enable_pdf_renderer,
            "backend": "xelatex",
            "dependency_available": xelatex_available,
        },
    }


def build_report_binary_response(
    workbook_path: str | Path,
    query: dict[str, list[str]],
    output_format: str,
    *,
    enable_pdf_renderer: bool = False,
) -> tuple[bytes, str, str]:
    report = _report_from_query(workbook_path, query)
    filename_stem = f"eco-report-{report.fid}-{report.target_year}"
    if output_format == "docx":
        return (
            build_docx_report(report),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename_stem + ".docx",
        )
    if output_format != "pdf":
        raise ValueError(f"Unsupported report format: {output_format}")
    capabilities = renderer_capabilities(enable_pdf_renderer)
    if not capabilities["pdf"]["available"]:
        raise RendererUnavailableError("PDF renderer is disabled or xelatex is unavailable")
    with tempfile.TemporaryDirectory(prefix="eco-report-service-") as directory:
        output = write_pdf_report(report, Path(directory) / (filename_stem + ".pdf"))
        payload = output.read_bytes()
    return payload, "application/pdf", filename_stem + ".pdf"


class EcoReportRequestHandler(BaseHTTPRequestHandler):
    workbook_path: Path
    service_token: str
    enable_pdf_renderer = False
    server_version = "EcoReportService/0.1"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: HTTPStatus, payload: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _binary(self, status: HTTPStatus, payload: bytes, content_type: str, filename: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Report-Service-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, self.service_token)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "eco-report-api",
                    "renderers": renderer_capabilities(self.enable_pdf_renderer),
                },
            )
            return
        if parsed.path not in {
            "/v1/capabilities",
            "/v1/report-ir",
            "/v1/report-package",
            "/v1/report-markdown",
            "/v1/report-docx",
            "/v1/report-pdf",
        }:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            query = parse_qs(parsed.query)
            if parsed.path == "/v1/capabilities":
                response_payload = {"renderers": renderer_capabilities(self.enable_pdf_renderer)}
            elif parsed.path == "/v1/report-package":
                response_payload = build_report_package_response(self.workbook_path, query)
            elif parsed.path == "/v1/report-markdown":
                report_ir = _report_from_query(self.workbook_path, query)
                self._text(HTTPStatus.OK, render_markdown_report(report_ir))
                return
            elif parsed.path in {"/v1/report-docx", "/v1/report-pdf"}:
                output_format = "docx" if parsed.path.endswith("docx") else "pdf"
                payload, content_type, filename = build_report_binary_response(
                    self.workbook_path,
                    query,
                    output_format,
                    enable_pdf_renderer=self.enable_pdf_renderer,
                )
                self._binary(HTTPStatus.OK, payload, content_type, filename)
                return
            else:
                response_payload = build_report_response(self.workbook_path, query)
        except RendererUnavailableError as exc:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "renderer_unavailable", "message": str(exc)},
            )
            return
        except (InputValidationError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(exc)})
            return
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})
            return
        self._json(HTTPStatus.OK, response_payload)

    def log_message(self, format: str, *args: object) -> None:
        # Headers are never logged. log_request() removes query parameters first.
        super().log_message(format, *args)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path_without_query = urlparse(self.path).path
        self.log_message(
            '"%s %s %s" %s %s',
            self.command,
            path_without_query,
            self.request_version,
            str(code),
            str(size),
        )


def serve(
    workbook_path: str | Path,
    service_token: str,
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_pdf_renderer: bool = False,
) -> None:
    path = Path(workbook_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not service_token:
        raise ValueError("ECO_REPORT_SERVICE_TOKEN must not be empty")

    class ConfiguredHandler(EcoReportRequestHandler):
        pass

    ConfiguredHandler.workbook_path = path
    ConfiguredHandler.service_token = service_token
    ConfiguredHandler.enable_pdf_renderer = enable_pdf_renderer
    server = ThreadingHTTPServer((host, port), ConfiguredHandler)
    server.serve_forever()
