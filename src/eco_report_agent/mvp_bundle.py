from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from .mvp_charts import build_report_charts

from .docx_report import render_docx_bytes, validate_docx_bytes
from .analysis_builder import OpenAICompatibleAnalysisBackend, OpenAICompatibleAnalysisConfig
from .llm_writer import OpenAICompatibleNarrativeBackend, OpenAICompatibleWriterConfig
from .mvp import build_report_ir_mvp, validate_report_ir_mvp
from .mvp_writer import narrative_quality_warnings, validate_writer_output, write_report
from .pdf_report import write_pdf_markdown


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_mvp_review_bundle(
    workbook_path: str | Path,
    project_type: str,
    fid: int,
    target_year: int | None,
    project_name: str | None,
    output_directory: str | Path,
    *,
    generated_date: str | None = None,
    prepared_by: str = "生态状况评估编制组",
    writer_mode: str = "template",
    writer_base_url: str | None = None,
    writer_model: str | None = None,
    writer_api_key_env: str = "ECO_REPORT_WRITER_API_KEY",
    analysis_mode: str | None = None,
    analysis_base_url: str | None = None,
    analysis_model: str | None = None,
    analysis_api_key_env: str | None = None,
) -> Path:
    bundle_started = time.perf_counter()
    stage_durations: dict[str, float] = {}
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=False)
    effective_analysis_mode = analysis_mode or ("llm" if writer_mode == "llm" else "seed")
    if effective_analysis_mode not in {"seed", "llm"}:
        raise ValueError(f"unsupported analysis_mode: {effective_analysis_mode}")
    analysis_backend = None
    if effective_analysis_mode == "llm":
        base_url = analysis_base_url or writer_base_url
        model = analysis_model or writer_model
        if not base_url or not model:
            raise ValueError("analysis base URL and model are required in llm mode")
        analysis_backend = OpenAICompatibleAnalysisBackend(OpenAICompatibleAnalysisConfig(
            base_url=base_url,
            model=model,
            api_key_env=analysis_api_key_env or writer_api_key_env,
        ))
    stage_started = time.perf_counter()
    report = build_report_ir_mvp(
        workbook_path,
        project_type,
        fid,
        target_year,
        project_name,
        generated_date=generated_date,
        prepared_by=prepared_by,
        analysis_backend=analysis_backend,
    )
    stage_durations["report_ir_build_seconds"] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    report["report_assets"]["charts"] = build_report_charts(report, output / "charts")
    stage_durations["chart_render_seconds"] = round(time.perf_counter() - stage_started, 3)
    if writer_mode not in {"template", "llm"}:
        raise ValueError(f"unsupported writer_mode: {writer_mode}")
    backend = None
    if writer_mode == "llm":
        if not writer_base_url or not writer_model:
            raise ValueError("writer_base_url and writer_model are required in llm mode")
        backend = OpenAICompatibleNarrativeBackend(OpenAICompatibleWriterConfig(
            base_url=writer_base_url,
            model=writer_model,
            api_key_env=writer_api_key_env,
        ))
    stage_started = time.perf_counter()
    writer = write_report(report, backend)
    stage_durations["writer_stage_seconds"] = round(time.perf_counter() - stage_started, 3)
    ir_issues = validate_report_ir_mvp(report)
    writer_issues = validate_writer_output(writer.markdown, report)
    _write_json(output / "report_ir.json", report)
    _write_json(output / "indicator_facts.json", report["indicator_facts"])
    _write_json(output / "problem_diagnoses.json", report["problem_diagnoses"])
    _write_json(output / "cross_problem_analysis.json", report["cross_problem_analysis"])
    _write_json(output / "measure_recommendations.json", report["measure_recommendations"])
    if writer.draft is not None:
        _write_json(output / "report_draft.json", writer.draft)
    if writer.rejected_draft is not None:
        _write_json(output / "report_draft_rejected.json", writer.rejected_draft)
    (output / "report.md").write_text(writer.markdown, encoding="utf-8")
    name = report["project"]["name"]
    year = report["report_metadata"]["current_year"]
    stage_started = time.perf_counter()
    docx_payload = render_docx_bytes(writer.markdown, name, year)
    docx_issues = list(validate_docx_bytes(docx_payload))
    (output / "report.docx").write_bytes(docx_payload)
    stage_durations["docx_render_seconds"] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    write_pdf_markdown(writer.markdown, name, year, output / "report.pdf")
    stage_durations["pdf_render_seconds"] = round(time.perf_counter() - stage_started, 3)
    writer_gate_issues = []
    if writer_mode == "llm" and writer.backend != "llm":
        writer_gate_issues.append("requested_llm_writer_fell_back")
    analysis_gate_issues = []
    if effective_analysis_mode == "llm" and report["audit"].get("analysis_backend") != "llm":
        analysis_gate_issues.append("requested_llm_analysis_missing")
    quality_warnings = narrative_quality_warnings(writer.draft, report) if writer.draft is not None else []
    analysis_request_durations = report["audit"].get("analysis_request_durations_seconds", [])
    writer_request_durations = list(getattr(backend, "request_durations_seconds", []))
    analysis_request_count = int(report["audit"].get("analysis_request_count", 0))
    writer_request_count = int(getattr(backend, "request_count", 0))
    stage_durations["bundle_to_validation_seconds"] = round(time.perf_counter() - bundle_started, 3)
    validation = {
        "valid": not (ir_issues or writer_issues or docx_issues or writer_gate_issues or analysis_gate_issues),
        "report_ir_issues": ir_issues,
        "analysis_gate_issues": analysis_gate_issues,
        "writer_issues": writer_issues,
        "writer_gate_issues": writer_gate_issues,
        "writer_quality_warnings": quality_warnings,
        "docx_structural_issues": docx_issues,
        "analysis_backend": report["audit"].get("analysis_backend"),
        "analysis_fallback_problem_ids": report["audit"].get("analysis_fallback_problem_ids", []),
        "analysis_cross_fallback": report["audit"].get("analysis_cross_fallback", False),
        "analysis_request_count": analysis_request_count,
        "analysis_request_durations_seconds": analysis_request_durations,
        "analysis_request_seconds": round(sum(analysis_request_durations), 3),
        "analysis_model": analysis_model or writer_model if effective_analysis_mode == "llm" else None,
        "writer_backend": writer.backend,
        "writer_boundary_repaired": writer.boundary_repaired,
        "writer_request_count": writer_request_count,
        "writer_request_durations_seconds": writer_request_durations,
        "writer_request_seconds": round(sum(writer_request_durations), 3),
        "llm_request_count_total": analysis_request_count + writer_request_count,
        "llm_request_seconds_total": round(
            sum(analysis_request_durations) + sum(writer_request_durations), 3
        ),
        "stage_durations_seconds": stage_durations,
        "writer_model": writer_model if writer.backend == "llm" else None,
        "writer_fallback_reason": writer.fallback_reason,
    }
    _write_json(output / "validation_report.json", validation)
    comparison = """# V2.3 统一分析框架报告与基线对比说明

## 分析与写作变化

- 独立受约束 LLM Analysis Builder 不改变既定诊断，正式报告只使用突出问题和持续观察问题的分析素材。
- Writer 输入已排除数据不足和不适用问题，并通过输出边界清除规则编号、字段名和系统判定话术。
- 问题优先级评分与排序模块暂不进入报告链路；指标事实、规则、证据和措施来源仍保留稳定追溯字段。

## 渲染变化

- 封面、摘要和目录独立分页，封面不显示页眉页脚。
- DOCX 使用 TOC/PAGE 域，PDF 生成可解析目录页码。
- 全部表格使用标准三线表；图件改为低饱和科研配色，并同时输出 SVG、PDF 和 300 dpi PNG。
- 同一合格措施可追溯关联多个问题；无合规匹配时明确披露，不新增措施。
"""
    (output / "comparison.md").write_text(comparison, encoding="utf-8")
    checklist = """# 胜利矿报告人工审核清单

请分别标记“通过 / 需修改”，并填写意见：

- [ ] 目录与章节层次
- [ ] 突出问题分析篇幅与证据链
- [ ] 持续观察问题的审慎表达
- [ ] 指标趋势与诊断结论
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
    _write_json(output / "manifest.json", {"format_version": 5, "execution_location": "hpc-slurm", "review_gate": "pending_user_review" if validation["valid"] else "validation_failed", "project_type": project_type, "fid": fid, "target_year": year, "validation": validation, "files": files})
    if not validation["valid"]:
        raise ValueError("MVP review bundle failed validation")
    return output
