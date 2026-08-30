from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .models import ReportIR


FIXED_HEADINGS = (
    "摘要",
    "1 项目与数据概况",
    "1.1 项目基本信息",
    "1.2 本次评价指标",
    "2 综合生态状况",
    "3 生态问题逐项诊断",
    "4 生态问题诊断汇总",
    "5 生态修复策略建议",
    "5.1 策略选择原则",
    "6 综合修复建议",
    "7 结论",
)

FORBIDDEN_SECTIONS = ("修复效果监测建议", "修复指标目标值", "措施成效评价指标")


def build_report_plan(report: ReportIR) -> dict[str, Any]:
    diagnostic_sections = [
        {
            "problem": item.problem,
            "status": item.status,
            "evidence_level": item.evidence_level,
            "indicators": list(item.indicators),
            "rationale": item.rationale,
        }
        for item in report.diagnoses
        if item.status != "不适用"
    ]
    strategy_sections: list[dict[str, Any]] = []
    for diagnosis in report.diagnoses:
        measures = [item for item in report.selected_measures if item.problem == diagnosis.problem]
        if measures:
            strategy_sections.append(
                {
                    "problem": diagnosis.problem,
                    "status": diagnosis.status,
                    "measures": [asdict(item) for item in measures],
                }
            )
    return {
        "directory": list(FIXED_HEADINGS),
        "diagnostic_sections": diagnostic_sections,
        "strategy_sections": strategy_sections,
        "forbidden_sections": list(FORBIDDEN_SECTIONS),
    }


def build_writer_prompt(report: ReportIR) -> str:
    package = {"report_ir": report.to_dict(), "report_plan": build_report_plan(report)}
    payload = json.dumps(package, ensure_ascii=False, sort_keys=True)
    return f"""你是能源开发生态状况诊断与生态修复报告撰写器。

只能依据下方 REPORT_PACKAGE 写作，不得使用外部常识补全事实。

必须遵守：
1. 数值、年份、FID、风险等级和措施名称只能来自 REPORT_PACKAGE。
2. 生态问题只能来自 report_plan.diagnostic_sections。
3. A级证据可以写“数据显示/表明”；B级只能写“风险识别结果提示”。
4. 数据不足和不适用不得写成现场事实；多指标方向冲突必须保留差异。
5. 措施只能从 report_plan.strategy_sections 选择，每个问题1—3条，不得创造措施。
6. 不得反向用措施证明问题，不得虚构原因、面积、工程量、位置、物种、密度、剂量、经费、工期或定量目标。
7. 严格使用 report_plan.directory 的目录；不得增加 report_plan.forbidden_sections。
8. 每个问题包含“评价依据、指标变化、综合诊断、诊断状态”。
9. 只输出正式中文Markdown报告，不解释生成过程。

REPORT_PACKAGE:
{payload}
"""


def build_report_package(report: ReportIR) -> dict[str, Any]:
    return {
        "report_ir": report.to_dict(),
        "report_plan": build_report_plan(report),
        "writer_prompt": build_writer_prompt(report),
    }
