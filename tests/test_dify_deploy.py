from __future__ import annotations

import unittest
from pathlib import Path

from eco_report_agent.dify_deploy import build_console_auth_headers, build_import_payload
from eco_report_agent.dify_dsl import render_dsl


class DifyDeployTest(unittest.TestCase):
    template = Path(__file__).parents[1] / "dify" / "eco_report_workflow.template.yml"

    def test_import_payload_create_and_update(self) -> None:
        dsl_text = render_dsl(self.template, "http://eco-report-api:8000")
        create_payload = build_import_payload(dsl_text)
        update_payload = build_import_payload(dsl_text, "app-123")
        self.assertEqual(create_payload["mode"], "yaml-content")
        self.assertNotIn("app_id", create_payload)
        self.assertEqual(update_payload["app_id"], "app-123")

    def test_console_auth_requires_access_and_csrf_tokens(self) -> None:
        headers = build_console_auth_headers("access-token", "csrf-token")
        self.assertEqual(headers["Authorization"], "Bearer access-token")
        self.assertEqual(headers["X-CSRF-Token"], "csrf-token")
        self.assertEqual(headers["Cookie"], "csrf_token=csrf-token")
        with self.assertRaises(ValueError):
            build_console_auth_headers("access-token", "")
        with self.assertRaises(ValueError):
            build_console_auth_headers("", "csrf-token")


if __name__ == "__main__":
    unittest.main()
