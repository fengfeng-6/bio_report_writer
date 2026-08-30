from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


INDICATOR_LABELS = {
    "MEAN_NDVI": "归一化植被指数（NDVI）", "MEAN_NPP": "净初级生产力（NPP）",
    "CA": "斑块面积（CA）", "LPI": "最大斑块指数（LPI）", "NP": "斑块数量（NP）",
    "PD": "斑块密度（PD）", "ED": "边缘密度（ED）", "LSI": "景观形状指数（LSI）",
    "ERI": "生态风险指数（ERI）", "MEAN_VCI": "植被状况指数（VCI）",
    "MEAN_EVI": "增强型植被指数（EVI）", "MEAN_SIF": "日光诱导叶绿素荧光（SIF）",
    "MEAN_VCS_C": "植被固碳量（VCS-C）", "MEAN_VCS_CO2e": "植被固碳当量（VCS-CO2e）",
}
TREND_LABELS = {
    "overall_up": "总体上升", "overall_down": "总体下降", "fluctuating_upward": "波动上升",
    "fluctuating_downward": "波动下降", "rise_then_fall": "先升后降", "fall_then_rise": "先降后升",
    "stable": "基本稳定", "unknown": "无法判断",
}
PRIORITY_LABELS = {"P1": "优先修复", "P2": "重点关注", "P3": "持续观察", "P0": "暂不形成修复决策"}


@dataclass(frozen=True)
class WriterResult:
    markdown: str
    backend: str
    fallback_reason: str | None


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _priority_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["problem_id"]: item for item in report["priority_assessment"]}


def render_template_writer(report: dict[str, Any]) -> str:
    metadata = report["report_metadata"]
    project = report["project"]
    overall = report["overall_assessment"]
    priorities = _priority_map(report)
    diagnoses = report["problem_diagnoses"]
    facts = report["indicator_facts"]
    active = [item for item in diagnoses if priorities[item["problem_id"]]["priority_level"] in {"P1", "P2"}]
    observed = [item for item in diagnoses if priorities[item["problem_id"]]["priority_level"] == "P3"]
    deferred = [item for item in diagnoses if priorities[item["problem_id"]]["priority_level"] == "P0"]
    lines = [
        "# 生态状况智能诊断与修复决策报告", "",
        f"## {project['name']} {metadata['current_year']}年度生态诊断", "",
        f"项目编号（FID）：{project['fid']}", "",
        f"评价年份：{metadata['current_year']}", "", "<!-- pagebreak -->", "",
        "## 摘要", "",
        f"本报告基于{metadata['evaluation_start_year']}—{metadata['evaluation_end_year']}年项目指标构建标准化指标事实，并依据结构化问题规则完成生态问题诊断、优先级评价和修复措施匹配。当前综合风险等级为{overall['risk_level']}。",
        "报告中的趋势、状态、优先级和措施均由确定性程序生成；Writer仅负责组织文字，不改变任何诊断事实。", "",
        "## 目录", "",
        "1 项目与评价基础  \n2 综合生态状况  \n3 重点生态问题诊断  \n4 其他生态问题评价  \n5 生态问题综合诊断  \n6 生态修复策略  \n7 综合修复建议  \n8 结论  \n附录", "",
        "## 1 项目与评价基础", "", "### 1.1 项目基本信息", "",
        "| 项目 | 内容 |", "|---|---|", f"| 项目名称 | {project['name']} |", f"| FID | {project['fid']} |", f"| 项目类型 | {project['project_type']} |", f"| 评价时段 | {metadata['evaluation_start_year']}—{metadata['evaluation_end_year']}年 |", "",
        "### 1.2 数据完整性", "", "| 指标范围 | 内容 |", "|---|---|",
        "| 可用指标 | " + "、".join(report["data_profile"]["available_indicators"]) + " |",
        "| 数据不足指标 | " + ("、".join(report["data_profile"]["missing_indicators"]) or "无") + " |", "",
        "## 2 综合生态状况", "",
        f"{metadata['current_year']}年项目综合风险等级为{overall['risk_level']}。本次共识别{len(active)}项P1/P2问题、{len(observed)}项P3问题和{len(deferred)}项P0问题。P0仅表示当前不形成修复决策，不等同于不存在生态风险。", "",
        "### 2.1 指标概览", "", "| 指标 | 当前值 | 长期趋势 | 最近变化 | 数据质量 |", "|---|---:|---|---|---|",
    ]
    for fact in facts:
        current = fact["statistics"].get("current_value") if fact["statistics"] else None
        lines.append(f"| {INDICATOR_LABELS.get(fact['indicator'], fact['indicator'])} | {_number(current)} | {TREND_LABELS.get(fact['trend']['long_term'], fact['trend']['long_term'])} | {fact['trend']['recent']} | {fact['quality']['sufficiency']} |")
    for asset in report.get("report_assets", {}).get("charts", [])[:2]:
        lines.extend(["", f"![{asset['title']}]({asset['path']})", "", f"{asset['asset_id'].replace('figure-', '图 ').replace('-', '-')} {asset['title']}"])
    lines.extend(["", "## 3 重点生态问题诊断", ""])
    if not active:
        lines.extend(["当前未识别出P1或P2问题。", ""])
    fact_map = {item["indicator"]: item for item in facts}
    for index, diagnosis in enumerate(active, start=1):
        priority = priorities[diagnosis["problem_id"]]
        lines.extend([f"### 3.{index} {diagnosis['problem_name']}", "", f"诊断状态为“{diagnosis['status']}”，优先级为{priority['priority_level']}（{PRIORITY_LABELS[priority['priority_level']]}），证据等级为{diagnosis['evidence_level']}，可信度为{diagnosis['confidence']}。", "", "核心证据：", ""])
        for evidence in diagnosis["evidence"]:
            fact = fact_map[evidence["indicator"]]
            lines.append(f"- {INDICATOR_LABELS.get(evidence['indicator'], evidence['indicator'])}呈{TREND_LABELS.get(fact['trend']['long_term'], fact['trend']['long_term'])}，最近变化为{fact['trend']['recent']}；该证据对本问题的支持状态为{evidence['supports_problem']}。")
        lines.extend(["", "该结论仅依据上述指标事实和问题规则，不推断具体现场原因或工程参数。", ""])
    lines.extend(["## 4 其他生态问题评价", "", "### 4.1 持续观察问题", "", "| 问题 | 状态 | 优先级 | 趋势 | 证据等级 |", "|---|---|---|---|---|"])
    for item in observed:
        priority = priorities[item["problem_id"]]
        lines.append(f"| {item['problem_name']} | {item['status']} | {priority['priority_level']} | {item['trend']} | {item['evidence_level']} |")
    if not observed:
        lines.append("| 无 | — | — | — | — |")
    lines.extend(["", "### 4.2 数据不足与不适用问题", "", "| 问题 | 状态 | 优先级 | 限制说明 |", "|---|---|---|---|"])
    for item in deferred:
        limitations = "；".join(item["limitations"]) or "当前项目类型不适用"
        lines.append(f"| {item['problem_name']} | {item['status']} | P0 | {limitations} |")
    lines.extend(["", "## 5 生态问题综合诊断", "", "| 问题 | 状态 | 严重程度 | 问题趋势 | 可信度 | 优先级 | 得分 |", "|---|---|---|---|---|---|---:|"])
    for item in diagnoses:
        priority = priorities[item["problem_id"]]
        lines.append(f"| {item['problem_name']} | {item['status']} | {item['severity']} | {item['trend']} | {item['confidence']} | {priority['priority_level']} | {_number(priority['priority_score'])} |")
    matrix_assets = [asset for asset in report.get("report_assets", {}).get("charts", []) if asset["asset_id"] == "figure-5-1"]
    for asset in matrix_assets:
        lines.extend(["", f"![{asset['title']}]({asset['path']})", "", f"图 5-1 {asset['title']}"])
    lines.extend(["", "## 6 生态修复策略", "", "| 措施 | 对应问题 | 优先级 | 作用机制 | 参数状态 |", "|---|---|---|---|---|"])
    diagnosis_names = {item["problem_id"]: item["problem_name"] for item in diagnoses}
    for item in report["measure_recommendations"]:
        names = "、".join(diagnosis_names[problem_id] for problem_id in item["target_problem_ids"])
        lines.append(f"| {item['name']} | {names} | {item['priority']} | {item['mechanism']} | 未提供具体参数 |")
    if not report["measure_recommendations"]:
        lines.append("| 当前无合格措施 | — | — | — | — |")
    p1 = [item["problem_name"] for item in diagnoses if priorities[item["problem_id"]]["priority_level"] == "P1"]
    p2 = [item["problem_name"] for item in diagnoses if priorities[item["problem_id"]]["priority_level"] == "P2"]
    lines.extend(["", "## 7 综合修复建议", "", f"优先修复方向为：{'、'.join(p1) if p1 else '当前无P1问题'}。重点关注方向为：{'、'.join(p2) if p2 else '当前无P2问题'}。实施顺序应服从证据等级与优先级，具体位置、工程量、密度、经费和工期需在现场调查及工程设计后确定。", "", "## 8 结论", "", f"本次评价形成了{len(facts)}项指标事实、{len(diagnoses)}项问题诊断和{len(report['measure_recommendations'])}条受控措施建议。所有P0问题均未生成措施，且报告未补充输入之外的工程参数。", "", "## 附录 A 指标趋势结果", "", "| 指标 | 有效年份数 | 最小值年份 | 最大值年份 | 相对斜率 |", "|---|---:|---:|---:|---:|"])
    for fact in facts:
        stats = fact["statistics"]
        trend = fact["trend"]
        lines.append(f"| {fact['indicator']} | {fact['quality']['valid_year_count']} | {stats.get('minimum', {}).get('year', '—')} | {stats.get('maximum', {}).get('year', '—')} | {_number(trend.get('relative_slope'))} |")
    lines.extend(["", "## 附录 B 问题规则与证据追溯", "", "| 问题ID | 问题 | 证据引用 | 状态 |", "|---|---|---|---|"])
    for item in diagnoses:
        refs = "、".join(evidence["fact_ref"] for evidence in item["evidence"]) or "无"
        lines.append(f"| {item['problem_id']} | {item['problem_name']} | {refs} | {item['status']} |")
    lines.append("")
    return "\n".join(lines)


_PARAMETER_PATTERN = re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:米|公里|公顷|亩|吨|千克|公斤|万元|元|天|个月|株|克/平方米)")


def validate_writer_output(text: str, report: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    required_headings = ["1 项目与评价基础", "2 综合生态状况", "3 重点生态问题诊断", "4 其他生态问题评价", "5 生态问题综合诊断", "6 生态修复策略", "7 综合修复建议", "8 结论", "附录 A 指标趋势结果", "附录 B 问题规则与证据追溯"]
    for heading in required_headings:
        if heading not in text:
            issues.append({"code": "missing_heading", "message": heading})
    allowed_measures = {item["name"] for item in report["measure_recommendations"]}
    for audit in report["audit"]["measure_filter"]:
        name = audit.get("text", "")
        if name and name not in allowed_measures and name in text:
            issues.append({"code": "unapproved_measure", "message": name})
    for match in _PARAMETER_PATTERN.finditer(text):
        issues.append({"code": "invented_implementation_parameter", "message": match.group(0)})
    for item in report["problem_diagnoses"]:
        if item["status"] in {"数据不足", "不适用"} and re.search(re.escape(item["problem_name"]) + r".{0,60}(?:已发生|确定存在|严重恶化)", text):
            issues.append({"code": "deferred_problem_asserted", "message": item["problem_name"]})
    return issues


def write_report(report: dict[str, Any], llm_backend: Callable[[dict[str, Any], str], str] | None = None) -> WriterResult:
    baseline = render_template_writer(report)
    baseline_issues = validate_writer_output(baseline, report)
    if baseline_issues:
        raise ValueError("Template Writer validation failed: " + json.dumps(baseline_issues, ensure_ascii=False))
    if llm_backend is None:
        return WriterResult(baseline, "template", None)
    try:
        candidate = llm_backend(report, baseline)
        issues = validate_writer_output(candidate, report)
        if issues:
            return WriterResult(baseline, "template", "llm_validation_failed:" + json.dumps(issues, ensure_ascii=False))
        return WriterResult(candidate, "llm", None)
    except Exception as exc:
        return WriterResult(baseline, "template", f"llm_error:{type(exc).__name__}")
