from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


INDICATOR_LABELS = {
    "MEAN_NDVI": "归一化植被指数（NDVI）", "MEAN_NPP": "净初级生产力（NPP）",
    "MEAN_NEP": "净生态系统生产力（NEP）", "MEAN_NCS": "净碳汇（NCS）",
    "CA": "斑块面积（CA）", "LPI": "最大斑块指数（LPI）", "NP": "斑块数量（NP）",
    "PD": "斑块密度（PD）", "ED": "边缘密度（ED）", "LSI": "景观形状指数（LSI）",
    "ERI": "生态风险指数（ERI）", "MEAN_VCI": "植被状况指数（VCI）",
    "MEAN_EVI": "增强型植被指数（EVI）", "MEAN_SIF": "日光诱导叶绿素荧光（SIF）",
    "MEAN_VCS_C": "植被固碳量（VCS-C）", "MEAN_VCS_CO2e": "植被固碳当量（VCS-CO2e）",
    "MEAN_VCR": "植被碳储量比率（VCR）",
}
TREND_LABELS = {
    "overall_up": "总体上升", "overall_down": "总体下降", "fluctuating_upward": "波动上升",
    "fluctuating_downward": "波动下降", "rise_then_fall": "先升后降", "fall_then_rise": "先降后升",
    "stable": "基本稳定", "unknown": "无法判断", "up": "上升", "down": "下降",
    "worsening": "恶化", "recently_worsening": "近期恶化", "mixed": "方向不一致",
    "recently_improving": "近期改善", "improving": "改善",
}
SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低", "not_assessable": "不可评价"}
SUFFICIENCY_LABELS = {"sufficient": "充分", "insufficient": "不足"}
PROJECT_TYPE_LABELS = {
    "coal": "煤矿", "solar": "光伏", "photovoltaic": "光伏",
    "wind": "风电", "other": "其他",
}
SOURCE_RECORD_LABELS = {"workbook_measure_text": "工作簿措施文本", "ml_recommendation": "模型候选措施"}
LIMITATION_FIELD_LABELS = {
    "ca_landcover_type": "土地覆盖类型数据",
    "has_buffer_gradient": "缓冲区梯度数据",
    "historical_abandoned_mine": "历史遗留废弃矿区数据",
}
FORBIDDEN_WRITER_PATTERNS = (
    r"可能[^。；\n]{0,24}(?:反映|由于|导致|生效|受限于|源于|归因于)",
    r"可能[^。；\n]{0,32}(?:处于|来自|受到|关联)",
    r"通常反映|自然波动|局部环境因子|饱和点|污染压力|驱动机制|风险源解析|敏感性评估",
    r"未受显著负面干扰",
    r"(?:排查|建议)[^。；\n]{0,20}(?:因子|原因|开展|监测|监控|干预|投入)",
)
MACHINE_LANGUAGE_PATTERN = re.compile(
    r"支持状态为\s*(?:True|False)|analysis_context|supports_problem|PR-P\d+|"
    r"historical_abandoned_mine|ca_landcover_type|has_buffer_gradient|&#x20;|"
    r"诊断规则|规则指定|关键条件.{0,20}未满足|当前结论不可评估|ReportIR|"
    r"\bAgent\b|\bWriter\b|\bJSON\b",
    re.I,
)
PRIORITY_LANGUAGE_PATTERN = re.compile(r"优先级|评分因子|归入\s*P[0-3]|P[0-3]级")


@dataclass(frozen=True)
class WriterResult:
    markdown: str
    backend: str
    fallback_reason: str | None
    draft: dict[str, Any] | None = None
    rejected_draft: Any = None
    boundary_repaired: bool = False


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "—"
        if value and (abs(value) >= 1_000_000 or abs(value) < 0.01):
            return f"{value:.2e}"
        return f"{value:.2f}"
    return str(value)


def _limitation_text(value: str) -> str:
    for field, label in LIMITATION_FIELD_LABELS.items():
        value = value.replace(field, label)
    return value


OMITTED_PROBLEM_STATUSES = {"数据不足", "不适用"}


def _reportable_diagnoses(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in report["problem_diagnoses"] if item["status"] not in OMITTED_PROBLEM_STATUSES]


def _problem_group(item: dict[str, Any]) -> str:
    return "prominent" if item["status"] == "突出问题" else "observation"


def _signal_sentence(signal: dict[str, Any]) -> str:
    indicator = INDICATOR_LABELS.get(signal["indicator"], signal["indicator"])
    long_term = TREND_LABELS.get(signal["long_term"], signal["long_term"])
    recent = TREND_LABELS.get(signal["recent"], signal["recent"])
    if signal["supports_problem"] is True:
        relation = "构成当前诊断的支持证据"
    elif signal["supports_problem"] is False:
        relation = "与风险方向并不一致，构成需要保留的反向证据"
    else:
        relation = "当前不足以形成明确支持或反向判断"
    return f"{indicator}长期呈{long_term}、近期呈{recent}，{relation}。"


def _deterministic_problem_paragraphs(item: dict[str, Any]) -> list[str]:
    context = item["analysis_context"]
    signals = context["indicator_synthesis"]["supporting_signals"] + context["indicator_synthesis"]["conflicting_signals"]
    opening = (
        f"{item['problem_name']}诊断为“{item['status']}”，证据等级为{item['evidence_level']}、"
        f"可信度为{CONFIDENCE_LABELS.get(item['confidence'], item['confidence'])}。"
    )
    evidence_text = "".join(_signal_sentence(signal) for signal in signals)
    relation = context["temporal"]["long_recent_relation"]
    relation_text = {
        "divergent": "长期与近期信号存在差异，近期变化不能单独推翻长期判断。",
        "partially_divergent": "近期信号并不完全一致，因此综合结论保留差异并控制判断强度。",
        "consistent": "长期与近期信号总体一致，证据关系较为清晰。",
        "unknown": "现有证据不足以比较长期与近期关系。",
    }[relation]
    uncertainty = "".join(context["uncertainty"]["interpretation_limitations"] + context["uncertainty"]["unsupported_inferences"])
    restoration = context["restoration"]
    restoration_text = "已有合规候选措施与该问题关联。" if restoration["match_status"] == "matched" else "现有候选措施与知识库没有形成合规匹配，不据此补造修复措施。"
    paragraphs = [opening + evidence_text, relation_text + context["interpretation"]["primary_finding"] + uncertainty]
    paragraphs.append(restoration_text + restoration["implementation_limits"][0])
    return paragraphs


def validate_narrative_draft(draft: Any, report: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(draft, dict):
        return [{"code": "invalid_narrative_type", "message": type(draft).__name__}]
    issues: list[dict[str, str]] = []
    if set(draft) != {"summary", "sections"}:
        return [{"code": "invalid_draft_top_level", "message": ",".join(sorted(draft))}]
    summary = draft["summary"]
    if not isinstance(summary, dict) or set(summary) != {"paragraphs", "keywords"}:
        return [{"code": "invalid_summary", "message": "expected paragraphs and keywords"}]
    if not isinstance(summary["paragraphs"], list) or not summary["paragraphs"] or not all(isinstance(value, str) and value.strip() for value in summary["paragraphs"]):
        issues.append({"code": "invalid_summary_paragraphs", "message": "summary"})
    if not isinstance(summary["keywords"], list) or not 3 <= len(summary["keywords"]) <= 6:
        issues.append({"code": "invalid_keywords", "message": "keywords must contain 3-6 items"})
    if not isinstance(draft["sections"], list):
        issues.append({"code": "invalid_sections", "message": "expected array"})
        return issues
    section_map = {item.get("section_id"): item for item in draft["sections"] if isinstance(item, dict)}
    expected_sections = {"overall_assessment", "problem_analysis", "cross_problem", "restoration", "conclusion"}
    if set(section_map) != expected_sections or len(section_map) != len(draft["sections"]):
        issues.append({"code": "section_mismatch", "message": ",".join(sorted(str(value) for value in set(section_map) ^ expected_sections))})
        return issues
    for section_id, section in section_map.items():
        allowed_fields = {"section_id", "title", "paragraphs"} | ({"problem_blocks"} if section_id == "problem_analysis" else set())
        required_fields = {"section_id", "title", "problem_blocks"} if section_id == "problem_analysis" else {"section_id", "title", "paragraphs"}
        if not required_fields.issubset(section) or not set(section).issubset(allowed_fields) or not isinstance(section["title"], str) or not section["title"].strip():
            issues.append({"code": "invalid_section", "message": section_id})
            continue
        paragraphs = section.get("paragraphs", [])
        if not isinstance(paragraphs, list) or not all(isinstance(value, str) and value.strip() for value in paragraphs):
            issues.append({"code": "invalid_section_paragraphs", "message": section_id})
    blocks = section_map["problem_analysis"].get("problem_blocks", [])
    if not isinstance(blocks, list):
        issues.append({"code": "invalid_problem_blocks", "message": "expected array"})
        return issues
    block_map = {item.get("problem_id"): item for item in blocks if isinstance(item, dict)}
    diagnoses = {item["problem_id"]: item for item in _reportable_diagnoses(report)}
    if set(block_map) != set(diagnoses) or len(block_map) != len(blocks):
        issues.append({"code": "problem_block_mismatch", "message": ",".join(sorted(str(value) for value in set(block_map) ^ set(diagnoses)))})
        return issues
    hard_floors = {"prominent": 350, "observation": 80}
    for problem_id, block in block_map.items():
        if set(block) != {"problem_id", "title", "paragraphs"}:
            issues.append({"code": "invalid_problem_block", "message": problem_id})
            continue
        paragraphs = block["paragraphs"]
        if not isinstance(block["title"], str) or diagnoses[problem_id]["problem_name"] not in block["title"]:
            issues.append({"code": "invalid_problem_title", "message": problem_id})
        if not isinstance(paragraphs, list) or not paragraphs or not all(isinstance(value, str) and value.strip() for value in paragraphs):
            issues.append({"code": "invalid_problem_paragraphs", "message": problem_id})
            continue
        length = sum(len(value) for value in paragraphs)
        group = _problem_group(diagnoses[problem_id])
        if length < hard_floors[group]:
            issues.append({"code": "problem_analysis_too_shallow", "message": f"{problem_id}:{length}<{hard_floors[group]}"})
        conflicts = diagnoses[problem_id]["analysis_context"]["indicator_synthesis"]["conflicting_signals"]
        if conflicts:
            text = "".join(paragraphs)
            mentions_conflict = any(
                signal["indicator"] in text or INDICATOR_LABELS.get(signal["indicator"], "") in text
                for signal in conflicts
            )
            if not mentions_conflict or not re.search(r"但|然而|不一致|反向|未支持|相对缓解|不能", text):
                issues.append({"code": "counter_evidence_omitted", "message": problem_id})
    serialized = json.dumps(draft, ensure_ascii=False)
    machine_match = MACHINE_LANGUAGE_PATTERN.search(serialized)
    if machine_match:
        issues.append({"code": "machine_language", "message": machine_match.group(0)[:160]})
    if PRIORITY_LANGUAGE_PATTERN.search(serialized):
        issues.append({"code": "priority_language", "message": "priority module language leaked"})
    for pattern in FORBIDDEN_WRITER_PATTERNS:
        match = re.search(pattern, serialized)
        if match:
            issues.append({"code": "unsupported_inference_or_action", "message": match.group(0)})
    allowed_measures = {item["name"] for item in report["measure_recommendations"]}
    for audit in report["audit"]["measure_filter"]:
        name = audit.get("text", "")
        if name and name not in allowed_measures and name in serialized:
            issues.append({"code": "unapproved_measure", "message": name})
    return issues


def repair_narrative_boundary(draft: Any, report: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(draft, dict):
        return None
    repaired = json.loads(json.dumps(draft, ensure_ascii=False))

    def clean_text(value: str) -> str:
        parts = re.findall(r"[^。！？；\n]+[。！？；]?|\n+", value)
        kept = [
            part for part in parts
            if not any(re.search(pattern, part) for pattern in FORBIDDEN_WRITER_PATTERNS)
            and not MACHINE_LANGUAGE_PATTERN.search(part)
            and not PRIORITY_LANGUAGE_PATTERN.search(part)
        ]
        return "".join(kept).strip()

    def clean_paragraphs(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [cleaned for value in values if isinstance(value, str) and (cleaned := clean_text(value))]

    summary = repaired.get("summary")
    if isinstance(summary, dict):
        summary["paragraphs"] = clean_paragraphs(summary.get("paragraphs"))
        if not summary["paragraphs"]:
            summary["paragraphs"] = ["本报告依据结构化指标事实形成生态诊断，结论严格受现有证据边界约束。"]
        if isinstance(summary.get("keywords"), list):
            summary["keywords"] = [value for value in summary["keywords"] if isinstance(value, str) and clean_text(value) == value.strip()]
    diagnoses = {item["problem_id"]: item for item in _reportable_diagnoses(report)}
    safe_section_titles = {
        "overall_assessment": "综合生态状况",
        "problem_analysis": "生态问题分析",
        "cross_problem": "生态问题综合诊断",
        "restoration": "生态修复方向",
        "conclusion": "结论",
    }
    for section in repaired.get("sections", []) if isinstance(repaired.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        if not isinstance(section.get("title"), str) or MACHINE_LANGUAGE_PATTERN.search(section["title"]) or PRIORITY_LANGUAGE_PATTERN.search(section["title"]):
            section["title"] = safe_section_titles.get(section.get("section_id"), "生态分析")
        section["paragraphs"] = clean_paragraphs(section.get("paragraphs", []))
        for block in section.get("problem_blocks", []) if isinstance(section.get("problem_blocks"), list) else []:
            if not isinstance(block, dict):
                continue
            original_text = "".join(value for value in block.get("paragraphs", []) if isinstance(value, str))
            boundary_hit = (
                bool(MACHINE_LANGUAGE_PATTERN.search(original_text))
                or bool(PRIORITY_LANGUAGE_PATTERN.search(original_text))
                or any(re.search(pattern, original_text) for pattern in FORBIDDEN_WRITER_PATTERNS)
            )
            block["paragraphs"] = clean_paragraphs(block.get("paragraphs"))
            problem_id = block.get("problem_id")
            if problem_id in diagnoses:
                diagnosis = diagnoses[problem_id]
                block["title"] = diagnosis["problem_name"]
                hard_floor = 350 if _problem_group(diagnosis) == "prominent" else 80
                conflicts = diagnosis["analysis_context"]["indicator_synthesis"]["conflicting_signals"]
                cleaned_text = "".join(block["paragraphs"])
                counter_evidence_missing = bool(conflicts) and (
                    not any(signal["indicator"] in cleaned_text or INDICATOR_LABELS.get(signal["indicator"], "") in cleaned_text for signal in conflicts)
                    or not re.search(r"但|然而|不一致|反向|未支持|相对缓解|不能", cleaned_text)
                )
                if boundary_hit or counter_evidence_missing or len(cleaned_text) < hard_floor:
                    safe_paragraphs = clean_paragraphs(_deterministic_problem_paragraphs(diagnosis))
                    boundary_note = "现有证据边界不支持进一步解释具体原因或现场机制。"
                    while sum(len(value) for value in safe_paragraphs) < hard_floor:
                        safe_paragraphs.append(boundary_note)
                    block["paragraphs"] = safe_paragraphs
    return repaired


def narrative_quality_warnings(draft: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    recommended = {"prominent": (800, 1500), "observation": (200, 500)}
    diagnoses = {item["problem_id"]: item for item in _reportable_diagnoses(report)}
    problem_section = next(item for item in draft["sections"] if item["section_id"] == "problem_analysis")
    warnings = []
    for block in problem_section["problem_blocks"]:
        level = _problem_group(diagnoses[block["problem_id"]])
        length = sum(len(value) for value in block["paragraphs"])
        low, high = recommended[level]
        if length < low or length > high:
            warnings.append({"code": "soft_length_range", "message": f"{block['problem_id']}:{length}, recommended {low}-{high}"})
    return warnings


def render_template_writer(report: dict[str, Any], narrative: dict[str, Any] | None = None) -> str:
    metadata = report["report_metadata"]
    project = report["project"]
    overall = report["overall_assessment"]
    diagnoses = sorted(_reportable_diagnoses(report), key=lambda item: (0 if _problem_group(item) == "prominent" else 1, item["problem_id"]))
    facts = report["indicator_facts"]
    active = [item for item in diagnoses if _problem_group(item) == "prominent"]
    observed = [item for item in diagnoses if _problem_group(item) == "observation"]
    section_map = {item["section_id"]: item for item in narrative["sections"]} if narrative else {}
    block_map: dict[str, dict[str, Any]] = {}
    if narrative:
        ordered_blocks = section_map["problem_analysis"]["problem_blocks"]
        block_map = {item["problem_id"]: item for item in ordered_blocks}
    risk_available = overall.get("risk_level") not in {None, ""}
    default_summary = (
        f"本报告基于{metadata['evaluation_start_year']}—{metadata['evaluation_end_year']}年项目指标，"
        "对证据充分且适用于本项目的生态问题进行分析。"
    )
    if risk_available:
        default_summary += f"当前综合风险等级为{overall['risk_level']}。"
    else:
        default_summary += "基底数据未提供综合风险等级，本报告不据指标趋势另行推定。"
    summary_paragraphs = narrative["summary"]["paragraphs"] if narrative else [default_summary]
    keywords = narrative["summary"]["keywords"] if narrative else ["生态诊断", "指标趋势", "生态修复", "证据追溯"]
    lines = [
        "# 生态状况智能诊断与修复决策报告", "",
        f"{project['name']} {metadata['current_year']}年度生态诊断", "",
        f"项目编号（FID）：{project['fid']}", "", f"评价年份：{metadata['current_year']}", "",
        f"生成日期：{metadata.get('generated_date', '—')}", "", f"编制单位：{metadata.get('prepared_by', '生态状况评估编制组')}", "",
        "<!-- pagebreak -->", "", "## 摘要", "", *[value for paragraph in summary_paragraphs for value in (paragraph, "")],
        "报告中的指标趋势、问题状态和措施匹配均来自受控分析结果，正文不扩展现场事实或实施参数。", "",
        "**关键词：** " + "；".join(keywords), "", "<!-- pagebreak -->", "", "## 目录", "", "<!-- toc -->", "", "<!-- pagebreak -->", "",
        "## 1 项目与评价基础", "", "### 1.1 项目基本信息", "", "**表 1-1 项目基本信息**", "",
        "| 项目 | 内容 |", "|---|---|", f"| 项目名称 | {project['name']} |", f"| FID | {project['fid']} |", f"| 项目类型 | {PROJECT_TYPE_LABELS.get(project['project_type'], project['project_type'])} |", f"| 盟市 | {report.get('region', {}).get('盟市') or '—'} |", f"| 旗县 | {report.get('region', {}).get('旗县') or '—'} |", f"| 评价时段 | {metadata['evaluation_start_year']}—{metadata['evaluation_end_year']}年 |", "",
        "### 1.2 数据完整性", "", "**表 1-2 数据完整性**", "", "| 指标范围 | 内容 |", "|---|---|",
        "| 可用指标 | " + "、".join(report["data_profile"]["available_indicators"]) + " |",
        "| 数据不足指标 | " + ("、".join(report["data_profile"]["missing_indicators"]) or "无") + " |", "",
        "## 2 综合生态状况", "",
        (
            f"{metadata['current_year']}年项目综合风险等级为{overall['risk_level']}。"
            if risk_available else
            "基底数据未提供综合风险等级，本报告不生成总体风险等级。"
        )
        + f"本次报告讨论{len(active)}项突出问题和{len(observed)}项需要持续观察的问题。", "",
        "### 2.1 指标概览", "", "**表 2-1 指标趋势概览**", "", "| 指标 | 当前值 | 长期趋势 | 最近变化 | 数据质量 |", "|---|---:|---|---|---|",
    ]
    if narrative:
        insert_at = lines.index("### 2.1 指标概览")
        overview = [value for paragraph in section_map["overall_assessment"]["paragraphs"] for value in (paragraph, "")]
        lines[insert_at:insert_at] = overview
    for fact in facts:
        current = fact["statistics"].get("current_value") if fact["statistics"] else None
        lines.append(f"| {INDICATOR_LABELS.get(fact['indicator'], fact['indicator'])} | {_number(current)} | {TREND_LABELS.get(fact['trend']['long_term'], fact['trend']['long_term'])} | {TREND_LABELS.get(fact['trend']['recent'], fact['trend']['recent'])} | {SUFFICIENCY_LABELS.get(fact['quality']['sufficiency'], fact['quality']['sufficiency'])} |")
    risk = report.get("risk_evidence")
    if isinstance(risk, dict):
        current_risk = risk.get("current", {})
        values = current_risk.get("values", {})
        scope = risk.get("source_scope") or "无可用目标年空间范围"
        lines.extend([
            "", "### 2.2 综合生态风险证据", "",
            f"{metadata['current_year']}年综合生态风险等级为{overall.get('risk_level') or '数据不足，无法判定'}，当前数值来源范围为{scope}。", "",
            "**表 2-2 目标年风险构成**", "", "| LER | ES | EP | RER | CVR | CERI |", "|---:|---:|---:|---:|---:|---:|",
            "| " + " | ".join(_number(values.get(field)) for field in ("LER", "ES", "EP", "RER", "CVR", "CERI")) + " |", "",
            "**表 2-3 多空间范围综合风险趋势**", "", "| 年份 | CERI均值 | 有效空间范围数 |", "|---:|---:|---:|",
        ])
        for item in risk.get("annual_series", []):
            lines.append(f"| {item.get('year', '—')} | {_number(item.get('mean_ceri'))} | {item.get('valid_scope_count', '—')} |")
        lines.extend(["", "**表 2-4 目标年风险空间梯度**", "", "| 空间范围 | CERI | 风险等级 |", "|---|---:|---|"])
        for item in risk.get("spatial_gradient", []):
            lines.append(f"| {item.get('scope', '—')} | {_number((item.get('values') or {}).get('CERI'))} | {item.get('risk_level') or '—'} |")
        trend = risk.get("trend", {})
        if trend.get("significance_status") == "available" and trend.get("p_value") is not None:
            lines.append(f"| 趋势类型 | {trend.get('trend_type') or '—'}（P={_number(trend.get('p_value'))}） | — |")
        else:
            lines.extend(["", "趋势显著性未作判断；本报告仅使用年度均值、变化率、Sen斜率和表内趋势类型。"])
    for asset in report.get("report_assets", {}).get("charts", [])[:2]:
        lines.extend(["", f"![{asset['title']}]({asset['path']})", "", f"**{asset['caption']}**"])
    for asset in report.get("report_assets", {}).get("charts", []):
        if asset.get("asset_id") == "figure-2-3":
            lines.extend(["", f"![{asset['title']}]({asset['path']})", "", f"**{asset['caption']}**"])
    lines.extend(["", "## 3 突出生态问题", ""])
    if not active:
        lines.extend(["本次评价未识别到证据充分的突出问题。", ""])
    for index, diagnosis in enumerate(active, start=1):
        lines.extend([f"### 3.{index} {diagnosis['problem_name']}", ""])
        paragraphs = block_map[diagnosis["problem_id"]]["paragraphs"] if narrative else _deterministic_problem_paragraphs(diagnosis)
        for paragraph in paragraphs:
            lines.extend([paragraph, ""])
    lines.extend(["## 4 持续观察的潜在问题", "", "**表 4-1 持续观察问题汇总**", "", "| 问题 | 当前判断 | 趋势 | 证据等级 |", "|---|---|---|---|"])
    for item in observed:
        lines.append(f"| {item['problem_name']} | {item['status']} | {TREND_LABELS.get(item['trend'], item['trend'])} | {item['evidence_level']} |")
    if narrative and observed:
        lines.extend(["", "### 4.1 问题分析", ""])
        for item in observed:
            lines.extend([f"**{item['problem_name']}。**", ""])
            for paragraph in block_map[item["problem_id"]]["paragraphs"]:
                lines.extend([paragraph, ""])
    lines.extend(["", "## 5 生态问题综合诊断", ""])
    cross_problem_paragraphs = section_map["cross_problem"]["paragraphs"] if narrative else [report.get("cross_problem_analysis", {}).get("integrated_interpretation", "当前无可展开的跨问题综合分析。")]
    lines.extend([value for paragraph in cross_problem_paragraphs for value in (paragraph, "")])
    spatial = report.get("spatial_evidence")
    if isinstance(spatial, dict):
        main_scopes = sorted({
            value for value in spatial.get("main_indicator_scope_selection", {}).values()
            if value
        })
        landscape_scopes = sorted({
            value for value in spatial.get("landscape_indicator_scope_selection", {}).values()
            if value
        })
        lines.extend([
            "### 5.1 空间梯度与敏感区关系", "",
            "植被及生产力指标的核心序列取自"
            + ("、".join(main_scopes) if main_scopes else "可用的最近空间范围")
            + "；景观结构核心序列取自"
            + ("、".join(landscape_scopes) if landscape_scopes else "可用的最近空间范围")
            + "。其他缓冲区和对照区序列作为空间差异证据保留，不替代核心序列。", "",
        ])
        protected = spatial.get("protected_area_records", [])
        if protected:
            known_areas = [
                float(item["intersection_area_km2"])
                for item in protected
                if item.get("intersection_area_km2") is not None
            ]
            protected_types = sorted({
                str(item.get("protected_area_type", "")).strip()
                for item in protected if item.get("protected_area_type")
            })
            lines.extend([
                "保护区空间关系表记录"
                + "、".join(protected_types)
                + "等类型的交叠信息，记录面积合计为"
                + (_number(sum(known_areas)) + "平方公里" if known_areas else "未提供")
                + "。"
                + "该数据未提供年份，因此只作为现有空间记录，不解释为时间趋势。", "",
            ])
        else:
            lines.extend([
                "保护区空间关系表中未记录该项目；此处按“未记录”处理，不表述为零侵占。", "",
            ])
    lines.extend(["**表 5-1 生态问题综合诊断**", "", "| 问题 | 问题类别 | 当前判断 | 严重程度 | 问题趋势 | 可信度 |", "|---|---|---|---|---|---|"])
    for item in diagnoses:
        category = "突出问题" if _problem_group(item) == "prominent" else "持续观察"
        lines.append(f"| {item['problem_name']} | {category} | {item['status']} | {SEVERITY_LABELS.get(item['severity'], item['severity'])} | {TREND_LABELS.get(item['trend'], item['trend'])} | {CONFIDENCE_LABELS.get(item['confidence'], item['confidence'])} |")
    for asset in report.get("report_assets", {}).get("charts", []):
        if asset["asset_id"] == "figure-5-1":
            lines.extend(["", f"![{asset['title']}]({asset['path']})", "", f"**{asset['caption']}**"])
    lines.extend(["", "## 6 生态修复策略", "", "**表 6-1 受控修复措施及来源**", "", "| 措施ID | 措施 | 对应问题 | 作用机制 | 来源 |", "|---|---|---|---|---|"])
    diagnosis_names = {item["problem_id"]: item["problem_name"] for item in diagnoses}
    for item in report["measure_recommendations"]:
        names = "、".join(diagnosis_names[problem_id] for problem_id in item["target_problem_ids"])
        lines.append(f"| {item['measure_id']} | {item['name']} | {names} | {item['mechanism']} | {SOURCE_RECORD_LABELS.get(item['source_record'], item['source_record'])} |")
    measure_not_provided = report.get("audit", {}).get("measure_input_status") == "not_provided"
    if measure_not_provided:
        lines.extend([
            "",
            "本次基底数据未提供项目候选修复措施，本报告不生成或补造具体修复措施。",
        ])
    active_without_measure = [item["problem_name"] for item in active if item["analysis_context"]["restoration"]["match_status"] == "no_compliant_match"]
    if active_without_measure:
        lines.extend(["", "现有候选措施和结构化知识库未能为" + "、".join(active_without_measure) + "形成合规匹配；本报告不据此新增措施。"])
    restoration_paragraphs = (
        ["本次基底数据未提供项目候选修复措施，现阶段不形成项目级修复措施建议。"]
        if measure_not_provided
        else section_map["restoration"]["paragraphs"] if narrative
        else ["现有措施只说明与相应生态问题的匹配关系。具体位置、工程量、密度、经费和工期需在现场调查及工程设计后确定。"]
    )
    conclusion_paragraphs = section_map["conclusion"]["paragraphs"] if narrative else [f"本次评价形成{len(facts)}项指标事实，正文讨论{len(diagnoses)}项生态问题，并列出{len(report['measure_recommendations'])}条已通过合规匹配的措施。"]
    lines.extend(["", "## 7 综合修复建议", ""])
    lines.extend([value for paragraph in restoration_paragraphs for value in (paragraph, "")])
    lines.extend(["## 8 结论", ""])
    lines.extend([value for paragraph in conclusion_paragraphs for value in (paragraph, "")])
    lines.extend(["## 附录 A 指标趋势结果", "", "**表 A-1 指标趋势与来源追溯**", "", "| 事实ID | 指标 | 有效年份数 | 最小值年份 | 最大值年份 | 相对斜率 |", "|---|---|---:|---:|---:|---:|"])
    for fact in facts:
        stats, trend = fact["statistics"], fact["trend"]
        lines.append(f"| {fact['fact_id']} | {fact['indicator']} | {fact['quality']['valid_year_count']} | {stats.get('minimum', {}).get('year', '—')} | {stats.get('maximum', {}).get('year', '—')} | {_number(trend.get('relative_slope'))} |")
    if isinstance(spatial, dict):
        lines.extend([
            "",
            "## 附录 C 分地类景观指标", "",
            "cls_1 至 cls_9 依次对应耕地、森林、灌木、草地、水域、裸地、建设用地、冰雪和湿地。", "",
            "**表 C-1 目标年份分地类景观指标**", "",
            "| 地类代码 | 地类名称 | 距离 | CA | LPI | NP | PD | ED | LSI |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ])
        for item in spatial.get("landscape_class_series", []):
            if item.get("year") != metadata["current_year"]:
                continue
            values = item.get("values", {})
            lines.append(
                f"| {item.get('class_code', item.get('category', '—'))} | {item.get('class_name', '类别未提供')} | {item.get('scope', '—')} | "
                f"{_number(values.get('CA'))} | {_number(values.get('LPI'))} | "
                f"{_number(values.get('NP'))} | {_number(values.get('PD'))} | "
                f"{_number(values.get('ED'))} | {_number(values.get('LSI'))} |"
            )
    lines.extend(["", "## 附录 B 证据与措施追溯", "", "**表 B-1 问题证据追溯**", "", "| 问题ID | 问题 | 证据ID | 当前判断 |", "|---|---|---|---|"])
    for item in diagnoses:
        refs = "、".join(evidence["evidence_id"] for evidence in item["evidence"]) or "无"
        lines.append(f"| {item['problem_id']} | {item['problem_name']} | {refs} | {item['status']} |")
    lines.append("")
    return "\n".join(lines)


_PARAMETER_PATTERN = re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:米|公里|公顷|亩|吨|千克|公斤|万元|元|天|个月|株|克/平方米)")


def validate_writer_output(text: str, report: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    required_headings = ["摘要", "目录", "1 项目与评价基础", "2 综合生态状况", "3 突出生态问题", "4 持续观察的潜在问题", "5 生态问题综合诊断", "6 生态修复策略", "7 综合修复建议", "8 结论", "附录 A 指标趋势结果", "附录 B 证据与措施追溯"]
    if report.get("risk_evidence") is not None:
        required_headings.append("2.2 综合生态风险证据")
    for heading in required_headings:
        if heading not in text:
            issues.append({"code": "missing_heading", "message": heading})
    for required_text in ("<!-- toc -->", "**关键词：**", "表 1-1"):
        if required_text not in text:
            issues.append({"code": "missing_report_element", "message": required_text})
    if any(item.get("asset_id") == "figure-5-1" for item in report.get("report_assets", {}).get("charts", [])) and "图 5-1" not in text:
        issues.append({"code": "missing_report_element", "message": "图 5-1"})
    if any(item.get("asset_id") == "figure-2-3" for item in report.get("report_assets", {}).get("charts", [])) and "图 2-3" not in text:
        issues.append({"code": "missing_report_element", "message": "图 2-3"})
    allowed_measures = {item["name"] for item in report["measure_recommendations"]}
    for audit in report["audit"]["measure_filter"]:
        name = audit.get("text", "")
        if name and name not in allowed_measures and name in text:
            issues.append({"code": "unapproved_measure", "message": name})
    for match in _PARAMETER_PATTERN.finditer(text):
        issues.append({"code": "invented_implementation_parameter", "message": match.group(0)})
    machine_match = re.search(r"支持状态为\s*(?:True|False)|analysis_context|supports_problem|PR-P\d+|historical_abandoned_mine|ca_landcover_type|has_buffer_gradient|&#x20;|诊断规则|规则指定|关键条件.{0,20}未满足|当前结论不可评估|ReportIR|\bAgent\b|\bWriter\b|\bJSON\b", text, re.I)
    if machine_match:
        issues.append({"code": "machine_language", "message": machine_match.group(0)[:160]})
    if re.search(r"优先级|评分因子|归入\s*P[0-3]|P[0-3]级|P[0-3](?!\d)", text):
        issues.append({"code": "priority_language", "message": "priority module language leaked"})
    if "综合风险等级为None" in text or "综合风险等级为null" in text:
        issues.append({"code": "missing_risk_level_leaked", "message": "null risk level"})
    for item in report["problem_diagnoses"]:
        if item.get("problem_id") == "P013" and report.get("risk_evidence") is not None:
            continue
        if item["status"] in OMITTED_PROBLEM_STATUSES and item["problem_name"] in text:
            issues.append({"code": "omitted_problem_leaked", "message": item["problem_name"]})
    return issues


def write_report(report: dict[str, Any], llm_backend: Callable[[dict[str, Any], str], Any] | None = None) -> WriterResult:
    baseline = render_template_writer(report)
    baseline_issues = validate_writer_output(baseline, report)
    if baseline_issues:
        raise ValueError("Template Writer validation failed: " + json.dumps(baseline_issues, ensure_ascii=False))
    if llm_backend is None:
        return WriterResult(baseline, "template", None, None, None)
    try:
        draft = llm_backend(report, baseline)
        boundary_repaired = False
        last_issues: list[dict[str, str]] = []
        revise = getattr(llm_backend, "revise", None)
        diagnosis_map = {item["problem_id"]: item for item in _reportable_diagnoses(report)}
        for revision_round in range(4):
            draft_issues = validate_narrative_draft(draft, report)
            quality_issues: list[dict[str, str]] = []
            if not draft_issues:
                quality_issues = [
                    issue for issue in narrative_quality_warnings(draft, report)
                    if issue["message"].split(":", 1)[0] in diagnosis_map
                    and _problem_group(diagnosis_map[issue["message"].split(":", 1)[0]]) == "prominent"
                ]
            candidate_issues: list[dict[str, str]] = []
            if not draft_issues and not quality_issues:
                candidate = render_template_writer(report, draft)
                candidate_issues = validate_writer_output(candidate, report)
                if not candidate_issues:
                    return WriterResult(candidate, "llm", None, draft, None, boundary_repaired)
            last_issues = draft_issues or quality_issues or candidate_issues
            if revision_round >= 3 or not callable(revise):
                break
            try:
                draft = revise(report, draft, last_issues)
            except Exception as exc:
                return WriterResult(baseline, "template", f"llm_revision_error:{type(exc).__name__}:{exc}", None, draft)
        # A failed LLM draft is not eligible for deterministic repair or delivery.
        return WriterResult(
            baseline,
            "template",
            "llm_validation_failed:" + json.dumps(last_issues, ensure_ascii=False),
            None,
            draft,
        )
    except Exception as exc:
        return WriterResult(baseline, "template", f"llm_error:{type(exc).__name__}:{exc}", None, None)
