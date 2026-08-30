from __future__ import annotations

import re

from .models import DataScope, MeasureDecision, ProjectType


PROHIBITED_FRAGMENTS: dict[ProjectType, tuple[str, ...]] = {
    ProjectType.COAL: ("设置光伏板", "风机基础"),
    ProjectType.SOLAR: ("煤矸石", "尾矿", "露天采坑", "塌陷坑", "风机基础"),
    ProjectType.WIND: ("设置光伏板", "光伏组件", "煤矸石", "尾矿", "露天采坑", "塌陷坑"),
}

# These measures depend on problem rules or monitoring evidence that the current
# V1 rule set does not provide. Keep them in the audit trail, never in正文.
UNAVAILABLE_RULE_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "重金属污染": ("重金属", "超富集", "耐污", "植-菌", "收割后无害化"),
}
V1_EXCLUDED_MONITORING_FRAGMENTS = ("监测", "动态评估", "复盘")
SITE_FEATURE_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "gangue_or_tailings": ("煤矸石", "矸石堆", "尾矿", "裸露堆体"),
    "open_pit_or_subsidence": ("露天采坑", "采坑", "塌陷坑"),
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ；;")


def filter_measures(
    raw: str,
    project_type: ProjectType | str,
    data_scope: DataScope | None = None,
) -> list[MeasureDecision]:
    project = ProjectType(project_type)
    scope = data_scope or DataScope()
    decisions: list[MeasureDecision] = []
    seen: set[str] = set()
    for item in re.split(r"[；;]", raw):
        normalized = _normalize(item)
        if not normalized:
            continue
        source = "ml_recommendation" if normalized.startswith("[ML推荐]") else "workbook_measure_text"
        normalized = _normalize(normalized.removeprefix("[ML推荐]"))
        if not normalized:
            continue
        if normalized in seen:
            decisions.append(MeasureDecision(normalized, False, "duplicate", source))
            continue
        seen.add(normalized)
        conflict = next(
            (fragment for fragment in PROHIBITED_FRAGMENTS[project] if fragment in normalized),
            None,
        )
        if conflict:
            decisions.append(
                MeasureDecision(normalized, False, f"project_type_conflict:{conflict}", source)
            )
            continue
        unavailable_rule = next(
            (
                problem
                for problem, fragments in UNAVAILABLE_RULE_FRAGMENTS.items()
                if any(fragment in normalized for fragment in fragments)
            ),
            None,
        )
        if unavailable_rule:
            decisions.append(
                MeasureDecision(
                    normalized,
                    False,
                    f"requires_unavailable_problem_rule:{unavailable_rule}",
                    source,
                )
            )
            continue
        monitoring_fragment = next(
            (fragment for fragment in V1_EXCLUDED_MONITORING_FRAGMENTS if fragment in normalized),
            None,
        )
        if monitoring_fragment:
            decisions.append(
                MeasureDecision(
                    normalized,
                    False,
                    f"v1_monitoring_measure_excluded:{monitoring_fragment}",
                    source,
                )
            )
            continue
        required_feature = next(
            (
                feature
                for feature, fragments in SITE_FEATURE_FRAGMENTS.items()
                if any(fragment in normalized for fragment in fragments)
            ),
            None,
        )
        feature_available = {
            "gangue_or_tailings": scope.has_gangue_or_tailings,
            "open_pit_or_subsidence": scope.has_open_pit_or_subsidence,
        }
        if required_feature and feature_available[required_feature] is not True:
            decisions.append(
                MeasureDecision(
                    normalized,
                    False,
                    f"requires_site_feature:{required_feature}",
                    source,
                )
            )
            continue
        decisions.append(MeasureDecision(normalized, True, "accepted", source))
    return decisions
