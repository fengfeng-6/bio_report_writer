from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eco_report_agent.markdown_report import render_markdown_report
from eco_report_agent.pipeline import build_report_ir
from eco_report_agent.report_validation import validate_report_markdown

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


class MarkdownReportTest(unittest.TestCase):
    def test_generated_report_passes_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.4, "植被退化", "乡土植被恢复；设置光伏板"), _row(2025, 0.2, "植被退化、土壤退化", "乡土植被恢复；设置光伏板")],
            )
            report = build_report_ir(workbook, "coal", 33, 2025, "测试项目")
            markdown = render_markdown_report(report)
        result = validate_report_markdown(markdown, report)
        self.assertTrue(result.valid, result.issues)
        self.assertNotIn("设置光伏板", markdown)
        self.assertNotIn("土壤退化", markdown)
        self.assertIn("## 7 结论", markdown)

    def test_validator_rejects_forbidden_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(2025, 0.2, "植被退化", "设置光伏板")])
            report = build_report_ir(workbook, "coal", 33, 2025)
        bad = "## 修复效果监测建议\n设置光伏板，并建设50米工程。"
        result = validate_report_markdown(bad, report)
        codes = {issue.code for issue in result.issues}
        self.assertIn("forbidden_section", codes)
        self.assertIn("rejected_measure_present", codes)
        self.assertIn("invented_implementation_parameter", codes)


if __name__ == "__main__":
    unittest.main()
