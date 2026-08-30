from __future__ import annotations

from dataclasses import dataclass

from .models import DataScope, ProblemDiagnosis, ProjectType, TrendEvidence


@dataclass(frozen=True)
class ProblemRule:
    name: str
    indicators: tuple[str, ...]
    risk_aliases: tuple[str, ...] = ()
    scope_requirement: str | None = None


PROBLEM_RULES: tuple[ProblemRule, ...] = (
    ProblemRule("工程扰动引发植被退化", ("MEAN_NDVI", "MEAN_NPP"), ("植被退化",)),
    ProblemRule("生态系统固碳能力下降", ("MEAN_NPP",), ("固碳能力下降",)),
    ProblemRule("原生草原面积缩减", ("CA", "LPI"), scope_requirement="native_grassland"),
    ProblemRule("景观破碎化", ("NP", "PD"), ("景观破碎化",)),
    ProblemRule("景观边缘效应", ("ED", "LSI"), ("边缘效应",), "none"),
    ProblemRule("土地沙化", ("MEAN_NDVI", "CA", "NP", "PD"), ("荒漠化", "土地沙化")),
    ProblemRule("生态修复成效", ("MEAN_NDVI", "MEAN_NPP", "CA", "LPI", "ERI")),
    ProblemRule("栖息地完整性受损", ("LPI", "NP", "PD", "ED", "LSI")),
    ProblemRule("缓冲区生态溢出", ("MEAN_NDVI", "MEAN_NPP", "NP", "PD", "ED", "LSI", "ERI"), scope_requirement="buffer_gradient"),
    ProblemRule("复杂人为镶嵌景观", ("ED", "LSI", "NP", "PD")),
    ProblemRule("光伏治沙双向效应", ("MEAN_NDVI", "MEAN_NPP", "CA"), scope_requirement="solar"),
    ProblemRule("历史遗留废弃矿区损害", ("MEAN_NDVI", "MEAN_NPP", "CA", "NP", "PD", "LPI", "ERI"), scope_requirement="historical_mine"),
    ProblemRule("综合生态风险", ("ERI",), ("综合生态风险",)),
)


HIGHER_IS_BETTER = {
    "MEAN_NDVI", "MEAN_NPP", "CA", "LPI", "MEAN_VCI", "MEAN_EVI",
    "MEAN_SIF", "MEAN_VCS_C", "MEAN_VCS_CO2e",
}
HIGHER_IS_WORSE = {"NP", "PD", "ED", "LSI", "ERI"}


def _applicability(
    rule: ProblemRule,
    project_type: ProjectType,
    scope: DataScope,
) -> tuple[bool | None, str]:
    if rule.scope_requirement == "native_grassland" and scope.ca_landcover_type != "native_grassland":
        return None, "CA/LPI统计对象未确认是原生草原"
    if rule.scope_requirement == "buffer_gradient" and not scope.has_buffer_gradient:
        return None, "缺少项目本体及1/3/5 km空间梯度数据"
    if rule.scope_requirement == "solar" and project_type != ProjectType.SOLAR:
        return False, "当前项目不是光伏项目"
    if rule.scope_requirement == "historical_mine":
        if scope.historical_abandoned_mine is False:
            return False, "输入明确不是历史遗留废弃矿区"
        if scope.historical_abandoned_mine is not True:
            return None, "缺少历史遗留废弃矿区属性"
    return True, ""


def _effect(indicator: str, direction: str) -> str:
    if direction == "stable":
        return "stable"
    if indicator in HIGHER_IS_BETTER:
        return "improving" if direction == "increase" else "adverse"
    if indicator in HIGHER_IS_WORSE:
        return "adverse" if direction == "increase" else "improving"
    return "unknown"


def build_diagnoses(
    project_type: ProjectType,
    reported_risk_types: list[str],
    trends: list[TrendEvidence],
    scope: DataScope,
) -> list[ProblemDiagnosis]:
    trend_map = {trend.indicator: trend for trend in trends}
    risk_tags = set(reported_risk_types)
    diagnoses: list[ProblemDiagnosis] = []
    for rule in PROBLEM_RULES:
        applicable, reason = _applicability(rule, project_type, scope)
        if applicable is False:
            diagnoses.append(ProblemDiagnosis(rule.name, rule.indicators, "不适用", "none", (), (), (), reason))
            continue
        if applicable is None:
            diagnoses.append(ProblemDiagnosis(rule.name, rule.indicators, "数据不足", "none", (), (), (), reason))
            continue
        effects = {
            indicator: _effect(indicator, trend_map[indicator].direction)
            for indicator in rule.indicators
            if indicator in trend_map
        }
        adverse = tuple(indicator for indicator, effect in effects.items() if effect == "adverse")
        improving = tuple(indicator for indicator, effect in effects.items() if effect == "improving")
        stable = tuple(indicator for indicator, effect in effects.items() if effect == "stable")
        has_risk_tag = rule.name in risk_tags or bool(risk_tags.intersection(rule.risk_aliases))
        if not effects:
            status = "存在风险" if has_risk_tag else "数据不足"
            level = "B" if has_risk_tag else "none"
            rationale = "风险识别结果提示该问题，但缺少可用核心指标" if has_risk_tag else "缺少可用核心指标"
        elif adverse and not improving:
            status = "突出问题"
            level = "A"
            rationale = "核心指标变化方向一致地指向恶化"
        elif improving and not adverse:
            status = "存在但改善中" if has_risk_tag else "未见明显恶化"
            level = "A"
            rationale = "核心指标总体改善" + ("，但风险标签仍存在" if has_risk_tag else "")
        elif adverse and improving:
            status = "存在风险"
            level = "A"
            rationale = "核心指标变化方向不一致，需保留差异"
        else:
            status = "存在风险" if has_risk_tag else "未见明显恶化"
            level = "B" if has_risk_tag else "A"
            rationale = "核心指标总体稳定" + ("，风险识别结果仍提示需关注" if has_risk_tag else "")
        diagnoses.append(
            ProblemDiagnosis(rule.name, rule.indicators, status, level, adverse, improving, stable, rationale)
        )
    return diagnoses
