from __future__ import annotations

import unittest
from pathlib import Path

from eco_report_agent.dify_dsl import DIFY_DSL_VERSION, render_dsl, validate_dsl_text


class DifyDslTest(unittest.TestCase):
    template = Path(__file__).parents[1] / "dify" / "eco_report_workflow.template.yml"

    def test_renders_dify_1_16_workflow(self) -> None:
        content = render_dsl(self.template, "http://eco-report-smoke-api:8000/")
        self.assertIn(f"version: {DIFY_DSL_VERSION}", content)
        self.assertIn("http://eco-report-smoke-api:8000/v1/report-markdown", content)
        self.assertIn("fid:900001", content)
        self.assertIn("target_year:2025", content)
        self.assertNotIn("#start.project_type#", content)
        self.assertEqual(validate_dsl_text(content), [])

    def test_rejects_relative_api_url(self) -> None:
        with self.assertRaises(ValueError):
            render_dsl(self.template, "/api")


if __name__ == "__main__":
    unittest.main()
