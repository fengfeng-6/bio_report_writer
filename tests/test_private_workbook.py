from __future__ import annotations

import os
import unittest

from eco_report_agent.pipeline import build_report_ir


class PrivateWorkbookTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ECO_REPORT_SOURCE_XLSX"), "private workbook not configured")
    def test_shengli_mine_fid_33(self) -> None:
        report = build_report_ir(os.environ["ECO_REPORT_SOURCE_XLSX"], "coal", 33, 2025)
        self.assertEqual(report.source_row, 307)
        self.assertEqual(report.risk_level, "III_中风险")
        self.assertNotIn("设置光伏板", report.accepted_measures)
        self.assertTrue(any(item.reason.startswith("project_type_conflict") for item in report.measure_audit))
        self.assertIn("unsupported_reported_risk_type:土壤退化", report.warnings)
        selected_text = "；".join(item.text for item in report.selected_measures)
        self.assertNotIn("重金属", selected_text)
        self.assertNotIn("耐污", selected_text)
        self.assertNotIn("监测", selected_text)
        self.assertNotIn("动态评估", selected_text)
        self.assertNotIn("矸石", selected_text)
        self.assertNotIn("尾矿", selected_text)


if __name__ == "__main__":
    unittest.main()
