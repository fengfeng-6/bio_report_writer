from __future__ import annotations

import unittest

from eco_report_agent.diagnosis import build_diagnoses
from eco_report_agent.models import DataScope, ProjectType, TrendEvidence


def _trend(indicator: str, direction: str) -> TrendEvidence:
    return TrendEvidence(indicator, 2017, 2025, 1.0, 2.0, 1.0, 0.1, direction, 9)


class DiagnosisTest(unittest.TestCase):
    def test_risk_tag_does_not_override_improving_series(self) -> None:
        diagnoses = build_diagnoses(
            ProjectType.COAL,
            ["植被退化"],
            [_trend("MEAN_NDVI", "increase"), _trend("MEAN_NPP", "increase")],
            DataScope(),
        )
        vegetation = next(item for item in diagnoses if item.problem == "工程扰动引发植被退化")
        self.assertEqual(vegetation.status, "存在但改善中")
        self.assertEqual(vegetation.evidence_level, "A")

    def test_scope_gates_are_enforced(self) -> None:
        diagnoses = build_diagnoses(ProjectType.COAL, [], [], DataScope())
        statuses = {item.problem: item.status for item in diagnoses}
        self.assertEqual(statuses["原生草原面积缩减"], "数据不足")
        self.assertEqual(statuses["缓冲区生态溢出"], "数据不足")
        self.assertEqual(statuses["光伏治沙双向效应"], "不适用")
        self.assertEqual(statuses["历史遗留废弃矿区损害"], "数据不足")


if __name__ == "__main__":
    unittest.main()
