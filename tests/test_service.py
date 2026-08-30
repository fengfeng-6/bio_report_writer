from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from eco_report_agent.ingest import InputValidationError
from eco_report_agent.service import (
    EcoReportRequestHandler,
    RendererUnavailableError,
    build_report_binary_response,
    build_report_package_response,
    build_report_response,
    renderer_capabilities,
)

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


class ServiceTest(unittest.TestCase):
    def test_build_report_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.3, "植被退化", "乡土植被恢复"), _row(2025, 0.4, "植被退化", "生态监测")],
            )
            payload = build_report_response(
                workbook,
                {"project_type": ["coal"], "fid": ["33"], "target_year": ["2025"]},
            )
        self.assertEqual(payload["fid"], 33)
        self.assertEqual(payload["target_year"], 2025)

    def test_build_report_package_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.3, "植被退化", "乡土植被恢复"), _row(2025, 0.4, "植被退化", "生态监测")],
            )
            package = build_report_package_response(
                workbook,
                {"project_type": ["coal"], "fid": ["33"], "target_year": ["2025"]},
            )
        self.assertIn("report_ir", package)
        self.assertIn("report_plan", package)
        self.assertIn("writer_prompt", package)

    def test_requires_integer_fid(self) -> None:
        with self.assertRaises(InputValidationError):
            build_report_response(
                "unused.xlsx",
                {"project_type": ["coal"], "fid": ["not-an-int"]},
            )

    def test_docx_binary_response_and_pdf_default_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.3, "植被退化", "乡土植被恢复"), _row(2025, 0.4, "植被退化", "生态监测")],
            )
            query = {
                "project_type": ["coal"],
                "fid": ["33"],
                "target_year": ["2025"],
                "project_name": ["测试项目"],
            }
            payload, content_type, filename = build_report_binary_response(workbook, query, "docx")
            self.assertTrue(payload.startswith(b"PK"))
            self.assertEqual(
                content_type,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assertEqual(filename, "eco-report-33-2025.docx")
            with self.assertRaises(RendererUnavailableError):
                build_report_binary_response(workbook, query, "pdf")

    def test_renderer_capabilities_are_explicit(self) -> None:
        capabilities = renderer_capabilities(False)
        self.assertTrue(capabilities["docx"]["available"])
        self.assertFalse(capabilities["pdf"]["available"])
        self.assertFalse(capabilities["pdf"]["enabled"])

    def test_http_download_auth_headers_and_pdf_503(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.3, "植被退化", "乡土植被恢复"), _row(2025, 0.4, "植被退化", "生态监测")],
            )

            class TestHandler(EcoReportRequestHandler):
                pass

            TestHandler.workbook_path = workbook
            TestHandler.service_token = "test-token"
            TestHandler.enable_pdf_renderer = False
            server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            query = "project_type=coal&fid=33&target_year=2025&project_name=test"
            try:
                with urlopen(base_url + "/health", timeout=5) as response:
                    health = json.loads(response.read())
                self.assertTrue(health["renderers"]["docx"]["available"])
                with self.assertRaises(HTTPError) as unauthorized:
                    urlopen(base_url + "/v1/report-docx?" + query, timeout=5)
                self.assertEqual(unauthorized.exception.code, 401)
                request = Request(
                    base_url + "/v1/report-docx?" + query,
                    headers={"X-Report-Service-Token": "test-token"},
                )
                with urlopen(request, timeout=5) as response:
                    payload = response.read()
                    headers = response.headers
                self.assertTrue(payload.startswith(b"PK"))
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(
                    headers["Content-Disposition"],
                    'attachment; filename="eco-report-33-2025.docx"',
                )
                pdf_request = Request(
                    base_url + "/v1/report-pdf?" + query,
                    headers={"X-Report-Service-Token": "test-token"},
                )
                with self.assertRaises(HTTPError) as unavailable:
                    urlopen(pdf_request, timeout=5)
                self.assertEqual(unavailable.exception.code, 503)
                error_payload = json.loads(unavailable.exception.read())
                self.assertEqual(error_payload["error"], "renderer_unavailable")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_access_log_omits_query_parameters(self) -> None:
        class TestHandler(EcoReportRequestHandler):
            pass

        TestHandler.workbook_path = Path("unused.xlsx")
        TestHandler.service_token = "test-token"
        TestHandler.enable_pdf_renderer = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        access_log = io.StringIO()
        try:
            with redirect_stderr(access_log):
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/health?fid=33&project_name=test",
                    timeout=5,
                ) as response:
                    self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertIn("/health", access_log.getvalue())
        self.assertNotIn("project_name", access_log.getvalue())
        self.assertNotIn("fid=", access_log.getvalue())


if __name__ == "__main__":
    unittest.main()
