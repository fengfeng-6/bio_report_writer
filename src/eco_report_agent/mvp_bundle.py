from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from .mvp_charts import build_report_charts

from .docx_report import render_docx_bytes, validate_docx_bytes
from .mvp import build_report_ir_mvp, validate_report_ir_mvp
from .mvp_writer import validate_writer_output, write_report
from .pdf_report import write_pdf_markdown


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_mvp_review_bundle(workbook_path: str | Path, project_type: str, fid: int, target_year: int | None, project_name: str | None, output_directory: str | Path) -> Path:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=False)
    report = build_report_ir_mvp(workbook_path, project_type, fid, target_year, project_name)
    report["report_assets"]["charts"] = build_report_charts(report, output / "charts")
    writer = write_report(report)
    ir_issues = validate_report_ir_mvp(report)
    writer_issues = validate_writer_output(writer.markdown, report)
    _write_json(output / "report_ir.json", report)
    _write_json(output / "indicator_facts.json", report["indicator_facts"])
    _write_json(output / "problem_diagnoses.json", report["problem_diagnoses"])
    _write_json(output / "priority_assessment.json", report["priority_assessment"])
    _write_json(output / "measure_recommendations.json", report["measure_recommendations"])
    (output / "report.md").write_text(writer.markdown, encoding="utf-8")
    name = report["project"]["name"]
    year = report["report_metadata"]["current_year"]
    docx_payload = render_docx_bytes(writer.markdown, name, year)
    docx_issues = list(validate_docx_bytes(docx_payload))
    (output / "report.docx").write_bytes(docx_payload)
    write_pdf_markdown(writer.markdown, name, year, output / "report.pdf")
    validation = {"valid": not (ir_issues or writer_issues or docx_issues), "report_ir_issues": ir_issues, "writer_issues": writer_issues, "docx_structural_issues": docx_issues, "writer_backend": writer.backend, "writer_fallback_reason": writer.fallback_reason}
    _write_json(output / "validation_report.json", validation)
    comparison = """# 新旧报告结构与诊断结果对比说明

## 结构变化

- 新版按照优先级控制篇幅，P1/P2进入重点问题章节，P3简述，P0集中表格展示。
- 新增独立封面、目录、其他问题评价、诊断矩阵式汇总和证据追溯附录。
- 不适用问题不再像旧版一样占用完整诊断章节。

## 数据与决策变化

- 趋势事实采用至少3个有效年份、相对斜率、相对极差和最近变化阈值。
- 问题诊断增加存在性、严重程度、问题趋势、可信度和证据一致性。
- 新增P0—P3优先级；P0不生成修复措施。
- 措施由候选列表与结构化知识表双重约束，不再以关键词作为最终依据。
"""
    (output / "comparison.md").write_text(comparison, encoding="utf-8")
    checklist = """# 胜利矿报告人工审核清单

请分别标记“通过 / 需修改”，并填写意见：

- [ ] 目录与章节层次
- [ ] P1/P2重点问题篇幅
- [ ] P3、数据不足和不适用问题的集中表达
- [ ] 指标趋势与诊断结论
- [ ] 优先级结果
- [ ] 修复措施及其对应关系
- [ ] 正式报告语言
- [ ] 表格与图表
- [ ] 字体、页边距、页眉页脚和分页
- [ ] 附录中的证据追溯
"""
    (output / "review_checklist.md").write_text(checklist, encoding="utf-8")
    files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = path.relative_to(output).as_posix()
            files[relative] = {"size": path.stat().st_size, "sha256": _sha256(path)}
    _write_json(output / "manifest.json", {"format_version": 2, "execution_location": "hpc-slurm", "review_gate": "pending_user_review", "project_type": project_type, "fid": fid, "target_year": year, "validation": validation, "files": files})
    if not validation["valid"]:
        raise ValueError("MVP review bundle failed validation")
    return output
