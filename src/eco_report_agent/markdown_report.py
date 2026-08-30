from __future__ import annotations

from collections import defaultdict

from .models import ProblemDiagnosis, ReportIR, TrendEvidence


PROJECT_TYPE_LABELS = {"solar": "光伏", "wind": "风电", "coal": "煤矿"}
INDICATOR_LABELS = {
    "MEAN_NDVI": "NDVI",
    "MEAN_NPP": "NPP",
    "CA": "CA",
    "LPI": "LPI",
    "NP": "NP",
    "PD": "PD",
    "ED": "ED",
    "LSI": "LSI",
    "MEAN_VCI": "VCI",
    "MEAN_EVI": "EVI",
    "MEAN_SIF": "SIF",
    "MEAN_VCS_C": "VCS-C",
    "MEAN_VCS_CO2e": "VCS-CO2e",
    "ERI": "ERI",
}
PATTERN_LABELS = {
    "overall_increase": "总体上升",
    "overall_decrease": "总体下降",
    "decrease_then_increase": "前降后升",
    "increase_then_decrease": "前升后降",
    "fluctuating_increase": "波动上升",
    "fluctuating_decrease": "波动下降",
    "stable": "基本稳定",
}
RECENT_LABELS = {"increase": "最近一年上升", "decrease": "最近一年下降", "stable": "最近一年基本稳定"}


def _number(value: object) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return "数据缺失" if value is None else str(value)


def _trend_sentence(trend: TrendEvidence, current_value: object) -> str:
    label = INDICATOR_LABELS.get(trend.indicator, trend.indicator)
    pattern = PATTERN_LABELS.get(trend.pattern, trend.direction)
    recent = RECENT_LABELS.get(trend.recent_direction, trend.recent_direction)
    sentence = (
        f"{trend.end_year}年{label}为{_number(current_value)}；"
        f"从{trend.start_year}—{trend.end_year}年变化看，指标呈{pattern}，{recent}。"
    )
    if trend.turning_years:
        years = "、".join(str(year) for year in trend.turning_years)
        sentence += f"时序方向变化出现在{years}年前后。"
    return sentence


def _diagnosis_conclusion(diagnosis: ProblemDiagnosis) -> str:
    if diagnosis.evidence_level == "A":
        lead = "现有时序指标支持"
    elif diagnosis.evidence_level == "B":
        lead = "风险识别结果提示"
    else:
        lead = "现有数据表明"
    if diagnosis.status == "数据不足":
        return f"现有数据不足以形成明确判断。{diagnosis.rationale}。"
    if diagnosis.status == "不适用":
        return f"本问题对当前项目不适用。{diagnosis.rationale}。"
    return f"{lead}本问题的诊断状态为“{diagnosis.status}”。{diagnosis.rationale}。"


def render_markdown_report(report: ReportIR) -> str:
    project_label = report.project_name or f"FID {report.fid}项目"
    project_type = PROJECT_TYPE_LABELS.get(report.project_type, report.project_type)
    trend_map = {trend.indicator: trend for trend in report.trends}
    lines: list[str] = [
        "# 生态状况诊断与生态修复建议报告",
        "",
        "## 摘要",
        "",
        f"本报告面向{project_label}，基于{report.years[0]}—{report.target_year}年年度指标、"
        f"规则库和{report.target_year}年风险识别结果开展诊断。当前风险等级为{report.risk_level}。"
        "报告中的问题结论仅来自适用规则和指标证据，修复措施仅来自经项目类型过滤后的候选列表。",
        "",
        "## 1 项目与数据概况",
        "",
        "### 1.1 项目基本信息",
        "",
        f"- 项目标识：{project_label}",
        f"- FID：{report.fid}",
        f"- 能源类型：{project_type}（{report.project_type}）",
        f"- 评价时段：{report.years[0]}—{report.target_year}年",
        f"- 当前评价年份：{report.target_year}年",
        "",
        "### 1.2 本次评价指标",
        "",
    ]
    available = [INDICATOR_LABELS.get(item.indicator, item.indicator) for item in report.trends]
    lines.append("本次评价使用具有至少两个有效年份的指标：" + "、".join(available) + "。")
    lines.extend(
        [
            "",
            "## 2 综合生态状况",
            "",
            f"{report.target_year}年风险等级为{report.risk_level}。"
            "风险标签在进入正文前经过规则适用性检查；未被问题规则支持的标签不作为本报告的生态问题事实。",
            "",
            "## 3 生态问题逐项诊断",
            "",
        ]
    )
    for index, diagnosis in enumerate(report.diagnoses, start=1):
        lines.extend(
            [
                f"### 3.{index} {diagnosis.problem}",
                "",
                "#### 评价依据",
                "",
                "本问题采用" + "、".join(INDICATOR_LABELS.get(name, name) for name in diagnosis.indicators) + "进行评价。",
                "",
                "#### 指标变化",
                "",
            ]
        )
        evidence_lines = []
        for indicator in diagnosis.indicators:
            trend = trend_map.get(indicator)
            if trend:
                evidence_lines.append(_trend_sentence(trend, report.latest_metrics.get(indicator)))
        lines.extend(evidence_lines or ["现有数据未形成满足本问题规则要求的有效时序证据。"])
        lines.extend(
            [
                "",
                "#### 综合诊断",
                "",
                _diagnosis_conclusion(diagnosis),
                "",
                f"诊断状态：{diagnosis.status}",
                "",
            ]
        )
    lines.extend(
        [
            "## 4 生态问题诊断汇总",
            "",
            "| 生态问题 | 对应指标 | 诊断状态 | 当前重点 |",
            "|---|---|---|---|",
        ]
    )
    for diagnosis in report.diagnoses:
        indicators = "、".join(INDICATOR_LABELS.get(name, name) for name in diagnosis.indicators)
        priority = "是" if diagnosis.status in {"突出问题", "存在风险"} and diagnosis.evidence_level == "A" else "否"
        lines.append(f"| {diagnosis.problem} | {indicators} | {diagnosis.status} | {priority} |")
    lines.extend(
        [
            "",
            "## 5 生态修复策略建议",
            "",
            "### 5.1 策略选择原则",
            "",
            "仅针对已确认或具有充分风险依据的问题选择候选措施；每个问题最多选择3条，"
            "不以措施反推现场问题，不生成输入中没有的实施参数。",
            "",
        ]
    )
    grouped: dict[str, list] = defaultdict(list)
    for selected in report.selected_measures:
        grouped[selected.problem].append(selected)
    section_number = 2
    for problem, measures in grouped.items():
        diagnosis = next(item for item in report.diagnoses if item.problem == problem)
        lines.extend([f"### 5.{section_number} 针对{problem}", ""])
        for measure in measures:
            lines.extend(
                [
                    f"#### {measure.text}",
                    "",
                    f"匹配理由：该措施来自当前项目候选列表，并与{problem}的诊断状态“{diagnosis.status}”相对应。",
                    "",
                    f"针对性建议：该措施在本项目中主要用于回应{problem}相关指标表现；"
                    "具体实施参数需由后续工程设计和现场资料确定。",
                    "",
                ]
            )
        section_number += 1
    if not grouped:
        lines.extend(["现有候选措施中未发现同时满足问题对应关系和项目类型要求的措施。", ""])
    priorities = [
        item.problem
        for item in report.diagnoses
        if item.status in {"突出问题", "存在风险"} and item.evidence_level == "A"
    ]
    priority_text = "、".join(priorities) if priorities else "现有数据未识别出具备A级恶化证据的重点问题"
    lines.extend(
        [
            "## 6 综合修复建议",
            "",
            f"当前优先关注方向为：{priority_text}。修复安排应以问题证据强度和候选措施匹配结果为依据，"
            "数据不足的问题暂不形成确定性工程结论。",
            "",
            "## 7 结论",
            "",
            f"{project_label}在{report.target_year}年的综合风险等级为{report.risk_level}。"
            "本报告已区分直接指标证据、风险提示和数据不足情形，并剔除与能源类型冲突或缺少问题依据的措施。"
            "后续正式文本可在保持ReportIR事实边界不变的前提下进行语言优化。",
            "",
        ]
    )
    return "\n".join(lines)
