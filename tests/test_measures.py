from __future__ import annotations

import unittest

from eco_report_agent.measures import filter_measures
from eco_report_agent.models import DataScope


class MeasuresTest(unittest.TestCase):
    def test_rejects_solar_measure_for_coal(self) -> None:
        result = filter_measures("乡土植被恢复；设置光伏板；乡土植被恢复", "coal")
        accepted = [item.text for item in result if item.accepted]
        rejected = [item for item in result if not item.accepted]
        self.assertEqual(accepted, ["乡土植被恢复"])
        self.assertEqual({item.reason for item in rejected}, {"project_type_conflict:设置光伏板", "duplicate"})

    def test_ml_prefix_is_audited(self) -> None:
        result = filter_measures("[ML推荐] 生态监测", "coal")
        self.assertEqual(result[0].source, "ml_recommendation")

    def test_rejects_measure_without_formal_problem_rule(self) -> None:
        result = filter_measures("选用耐污乡土植被钝化矿区重金属", "coal")
        self.assertFalse(result[0].accepted)
        self.assertEqual(result[0].reason, "requires_unavailable_problem_rule:重金属污染")

    def test_excludes_monitoring_measure_in_v1(self) -> None:
        result = filter_measures("长时序植被覆盖动态归因监测", "coal")
        self.assertFalse(result[0].accepted)
        self.assertEqual(result[0].reason, "v1_monitoring_measure_excluded:监测")

    def test_requires_explicit_site_feature(self) -> None:
        blocked = filter_measures("矸石堆覆土改良覆绿", "coal")
        allowed = filter_measures(
            "矸石堆覆土改良覆绿",
            "coal",
            DataScope(has_gangue_or_tailings=True),
        )
        self.assertEqual(blocked[0].reason, "requires_site_feature:gangue_or_tailings")
        self.assertTrue(allowed[0].accepted)


if __name__ == "__main__":
    unittest.main()
