from __future__ import annotations

import unittest

from eco_report_agent.metrics import compute_vci, compute_vcs, linear_slope, summarize_trend
from eco_report_agent.models import Observation, ProjectType


class MetricsTest(unittest.TestCase):
    def test_linear_slope(self) -> None:
        self.assertAlmostEqual(linear_slope([(2017, 1.0), (2018, 3.0), (2019, 5.0)]), 2.0)

    def test_vci_preserves_missing_values(self) -> None:
        self.assertEqual(compute_vci([0.2, None, 0.4]), [0.0, None, 100.0])

    def test_vcs_formula(self) -> None:
        vcs_c, vcs_co2e = compute_vcs(100.0, 0.5)
        self.assertAlmostEqual(vcs_c, 22.5)
        self.assertAlmostEqual(vcs_co2e, 82.5)

    def test_detects_decrease_then_increase(self) -> None:
        series = [
            Observation(ProjectType.COAL, 1, year, {"MEAN_NDVI": value}, index + 2)
            for index, (year, value) in enumerate([(2021, 0.4), (2022, 0.2), (2023, 0.3), (2024, 0.5)])
        ]
        trend = summarize_trend(series, "MEAN_NDVI")
        self.assertIsNotNone(trend)
        self.assertEqual(trend.pattern, "decrease_then_increase")
        self.assertEqual(trend.recent_direction, "increase")

    def test_treats_sub_per_mille_variation_as_numeric_stability(self) -> None:
        series = [
            Observation(ProjectType.COAL, 1, 2024, {"ED": 2.253824}, 2),
            Observation(ProjectType.COAL, 1, 2025, {"ED": 2.254137}, 3),
        ]
        trend = summarize_trend(series, "ED")
        self.assertIsNotNone(trend)
        self.assertEqual(trend.direction, "stable")
        self.assertEqual(trend.pattern, "stable")


if __name__ == "__main__":
    unittest.main()
