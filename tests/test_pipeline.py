from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eco_report_agent.pipeline import build_report_ir

from .xlsx_fixture import make_workbook


def _row(year: int, ndvi: float, risk: str, measures: str) -> dict[str, object]:
    return {
        "PROJECT_TYPE": "coal", "FID": 33, "YEAR": year, "MEAN_NDVI": ndvi,
        "STD_NDVI": 0.1, "MEAN_NPP": 0.2, "STD_NPP": 0.1, "CA": 100,
        "LPI": 60, "NP": 20, "PD": 2, "ED": 1.2, "LSI": 1.4,
        "MEAN_VCI": 50, "MEAN_EVI": 0.1, "MEAN_SIF": 0.01,
        "MEAN_VCS_C": None, "MEAN_VCS_CO2e": None, "ERI": 0.1,
        "LER": 0.2, "ES": 0.3, "EP": 0.4, "RER": 0.5, "CVR": 0.6,
        "RISK_LEVEL": "III_中风险", "RISK_LEVEL_CODE": 3,
        "RISK_TYPES": risk, "MEASURE_TEXT": measures,
        "ML_PREDICTION": "", "ML_ENABLED": "是",
    }


class PipelineTest(unittest.TestCase):
    def test_builds_auditable_report_ir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "fixture.xlsx",
                [
                    _row(2024, 0.3, "植被退化", "乡土植被恢复"),
                    _row(2025, 0.4, "植被退化、ML综合预测", "乡土植被恢复；设置光伏板"),
                ],
            )
            report = build_report_ir(path, "coal", 33, 2025)
        self.assertEqual(report.target_year, 2025)
        self.assertEqual(report.reported_risk_types, ["植被退化"])
        self.assertEqual(report.accepted_measures, ["乡土植被恢复"])
        self.assertEqual(report.selected_measures[0].problem, "工程扰动引发植被退化")
        self.assertIn("ml_recommendation_present:requires_rule_review", report.warnings)
        self.assertEqual(report.trends[0].direction, "increase")


if __name__ == "__main__":
    unittest.main()
