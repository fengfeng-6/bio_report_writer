from __future__ import annotations

import re

from .models import ProblemEvidence, TrendEvidence


PROBLEM_INDICATORS: dict[str, tuple[str, ...]] = {
    "植被退化": ("MEAN_NDVI", "MEAN_NPP"),
    "固碳能力下降": ("MEAN_NPP", "MEAN_VCS_C", "MEAN_VCS_CO2e"),
    "乡土草地面积变化": ("CA", "LPI"),
    "景观破碎化": ("NP", "PD", "LPI"),
    "边缘效应与风蚀风险": ("ED", "LSI"),
    "荒漠化": ("MEAN_NDVI", "CA", "NP", "PD"),
    "生境完整性下降": ("LPI", "NP", "PD", "ED", "LSI"),
    "复杂斑块镶嵌": ("ED", "LSI", "NP", "PD"),
    "历史矿山遗留影响": ("MEAN_NDVI", "MEAN_NPP", "CA", "NP", "PD", "LPI", "ERI"),
    "累计生态风险": ("ERI",),
}


def split_risk_types(raw: str) -> list[str]:
    items = [item.strip() for item in re.split(r"[、,，;；]", raw) if item.strip()]
    return [item for item in items if item != "ML综合预测"]


def build_problem_evidence(
    reported_risk_types: list[str],
    trends: list[TrendEvidence],
) -> list[ProblemEvidence]:
    trend_names = {trend.indicator for trend in trends}
    result: list[ProblemEvidence] = []
    for problem in reported_risk_types:
        indicators = PROBLEM_INDICATORS.get(problem)
        if indicators is None:
            continue
        result.append(
            ProblemEvidence(
                problem=problem,
                source="workbook_reported_risk_type",
                indicators=indicators,
                available_trends=tuple(name for name in indicators if name in trend_names),
            )
        )
    return result
