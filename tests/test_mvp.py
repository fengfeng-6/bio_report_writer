from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eco_report_agent.models import DataScope, Observation, ProjectType
from eco_report_agent.mvp import (
    build_indicator_fact,
    build_priority_assessment,
    build_problem_diagnoses,
    build_report_ir_mvp,
    load_json_config,
    validate_report_ir_mvp,
)
from eco_report_agent.mvp_writer import render_template_writer, validate_writer_output, write_report

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


def _observations(values: list[float]) -> list[Observation]:
    return [Observation(ProjectType.COAL, 33, 2017 + index, {"MEAN_NDVI": value}, index + 2) for index, value in enumerate(values)]


class IndicatorFactMvpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_json_config("trend_config.json")

    def test_expected_trend_patterns(self) -> None:
        cases = [
            ([1, 2, 3, 4, 5], "overall_up"),
            ([5, 4, 3, 2, 1], "overall_down"),
            ([10, 10.1, 9.9, 10], "stable"),
            ([1, 3, 2, 4, 3, 6], "fluctuating_upward"),
            ([6, 4, 5, 3, 4, 1], "fluctuating_downward"),
            ([1, 2, 4, 3, 1], "rise_then_fall"),
            ([4, 3, 1, 2, 5], "fall_then_rise"),
        ]
        for values, expected in cases:
            with self.subTest(expected=expected):
                fact = build_indicator_fact(_observations(values), "MEAN_NDVI", self.config)
                self.assertEqual(fact["trend"]["long_term"], expected)

    def test_less_than_three_years_is_unknown(self) -> None:
        fact = build_indicator_fact(_observations([1, 2]), "MEAN_NDVI", self.config)
        self.assertFalse(fact["valid"])
        self.assertEqual(fact["trend"]["long_term"], "unknown")


class MvpIntegrationTest(unittest.TestCase):
    def test_shengli_contract_scenarios(self) -> None:
        facts = [
            {"indicator": "MEAN_NDVI", "valid": True, "trend": {"long_term": "overall_up", "recent": "up"}},
            {"indicator": "MEAN_NPP", "valid": True, "trend": {"long_term": "overall_up", "recent": "up"}},
            {"indicator": "NP", "valid": True, "trend": {"long_term": "overall_up", "recent": "down"}},
            {"indicator": "PD", "valid": True, "trend": {"long_term": "overall_up", "recent": "down"}},
        ]
        diagnoses = build_problem_diagnoses("coal", ["植被退化"], facts, DataScope())
        by_id = {item["problem_id"]: item for item in diagnoses}
        self.assertEqual(by_id["P001"]["status"], "存在但改善中")
        self.assertEqual(by_id["P004"]["status"], "突出问题")
        self.assertEqual(by_id["P004"]["trend"], "recently_improving")
        self.assertEqual(by_id["P003"]["status"], "数据不足")
        self.assertEqual(by_id["P011"]["status"], "不适用")
        priorities = {item["problem_id"]: item for item in build_priority_assessment(diagnoses)}
        self.assertEqual(priorities["P003"]["priority_level"], "P0")
        self.assertEqual(priorities["P011"]["priority_level"], "P0")

    def test_report_ir_and_writer_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [
                    _row(2023, 0.2, "植被退化", "乡土种子库人工补播；设置光伏板"),
                    _row(2024, 0.3, "植被退化", "乡土种子库人工补播；设置光伏板"),
                    _row(2025, 0.4, "植被退化", "乡土种子库人工补播；设置光伏板"),
                ],
            )
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        self.assertEqual(report["schema_version"], "2.0-mvp")
        self.assertFalse(validate_report_ir_mvp(report))
        markdown = render_template_writer(report)
        self.assertFalse(validate_writer_output(markdown, report))
        self.assertNotIn("设置光伏板", markdown)
        self.assertNotIn("### 3.11 光伏治沙双向效应", markdown)
        self.assertIn("数据不足与不适用问题", markdown)

    def test_llm_invalid_output_falls_back(self) -> None:
        report = {
            "report_metadata": {"current_year": 2025, "evaluation_start_year": 2023, "evaluation_end_year": 2025},
            "project": {"name": "测试", "fid": 1, "project_type": "coal"},
            "overall_assessment": {"risk_level": "低"},
            "priority_assessment": [], "problem_diagnoses": [], "indicator_facts": [],
            "data_profile": {"available_indicators": [], "missing_indicators": []},
            "measure_recommendations": [], "audit": {"measure_filter": []},
        }
        result = write_report(report, lambda _report, _baseline: "无效输出")
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))


if __name__ == "__main__":
    unittest.main()
