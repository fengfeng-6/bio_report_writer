from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eco_report_agent.pipeline import build_report_ir
from eco_report_agent.report_plan import FORBIDDEN_SECTIONS, build_report_package, build_report_plan

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


class ReportPlanTest(unittest.TestCase):
    def test_fixed_directory_and_prompt_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [_row(2024, 0.2, "植被退化", "乡土植被恢复"), _row(2025, 0.1, "植被退化", "乡土植被恢复")],
            )
            report = build_report_ir(workbook, "coal", 33, 2025, "测试项目")
        plan = build_report_plan(report)
        package = build_report_package(report)
        self.assertEqual(plan["directory"][0], "摘要")
        self.assertEqual(plan["directory"][-1], "7 结论")
        self.assertEqual(plan["forbidden_sections"], list(FORBIDDEN_SECTIONS))
        self.assertIn("不得反向用措施证明问题", package["writer_prompt"])
        self.assertIn("测试项目", package["writer_prompt"])


if __name__ == "__main__":
    unittest.main()
