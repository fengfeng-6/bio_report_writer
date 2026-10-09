from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .analysis_builder import (
    CROSS_ANALYSIS_PROMPT,
    PROBLEM_ANALYSIS_PROMPT,
    OpenAICompatibleAnalysisBackend,
    OpenAICompatibleAnalysisConfig,
    build_analysis_material,
)
from .checkpointing import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    file_sha256,
    load_checkpoint,
    read_json,
    save_checkpoint,
    value_sha256,
)
from .docx_report import render_docx_bytes, validate_docx_bytes
from .llm_writer import (
    SYSTEM_PROMPT,
    OpenAICompatibleNarrativeBackend,
    OpenAICompatibleWriterConfig,
)
from .models import DataScope, Observation, ProjectType
from .mvp import (
    build_indicator_facts,
    build_measure_recommendations,
    build_problem_diagnoses,
    load_json_config,
    validate_report_ir_mvp,
)
from .mvp_charts import build_report_charts
from .mvp_writer import WriterResult, narrative_quality_warnings, validate_writer_output, write_report
from .pdf_report import write_pdf_markdown


MAIN_FIELD_MAP = {
    "NDVI": "MEAN_NDVI",
    "NPP": "MEAN_NPP",
    "NEP": "MEAN_NEP",
    "NCS": "MEAN_NCS",
    "EVI": "MEAN_EVI",
    "SIF": "MEAN_SIF",
    "VCI": "MEAN_VCI",
    "VCS": "MEAN_VCS_C",
    "VCR": "MEAN_VCR",
}
LANDSCAPE_DIAGNOSIS_FIELDS = ("LPI", "NP", "PD", "ED", "LSI")
MINIMUM_VALID_YEARS = 3
LANDSCAPE_CLASS_MAP = {
    "cls_1": "耕地", "cls_2": "森林", "cls_3": "灌木",
    "cls_4": "草地", "cls_5": "水域", "cls_6": "裸地",
    "cls_7": "建设用地", "cls_8": "冰雪", "cls_9": "湿地",
}
RISK_SCOPE_ORDER = ("本体", "0-1km", "1-3km", "3-5km", "5-10km")
RISK_FIELDS = ("LER", "ES", "EP", "RER", "CVR", "CERI_raw", "CERI")


def _build_risk_evidence(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    records = snapshot.get("risk_records") or []
    trend = snapshot.get("risk_trend_record")
    if not records and not trend and not snapshot.get("risk_source_present"):
        return None
    target = int(snapshot["target_year"])
    current = [item for item in records if item.get("year") == target]
    available = {str(item.get("scope", "")): item for item in current}
    ordered_scopes = RISK_SCOPE_ORDER if snapshot.get("project_type") in {"solar", "coal"} else RISK_SCOPE_ORDER[1:]
    selected = None
    source_scope = None
    for scope in ordered_scopes:
        item = available.get(scope)
        if item is not None and item.get("metrics", {}).get("CERI") is not None:
            selected, source_scope = item, scope
            break
    gradient = []
    for scope in ordered_scopes:
        item = available.get(scope)
        if item is None:
            continue
        gradient.append({
            "scope": scope,
            "risk_level": item.get("risk_level") or None,
            "risk_level_code": item.get("risk_level_code"),
            "values": {field: item.get("metrics", {}).get(field) for field in RISK_FIELDS},
            "source_row": item.get("source_row"),
        })
    annual_groups: dict[int, list[float]] = {}
    for item in records:
        value = item.get("metrics", {}).get("CERI")
        year = item.get("year")
        if value is None or year is None:
            continue
        annual_groups.setdefault(int(year), []).append(float(value))
    annual_series = [
        {
            "year": year,
            "mean_ceri": sum(values) / len(values),
            "valid_scope_count": len(values),
            "aggregation": "multi_scope_mean",
        }
        for year, values in sorted(annual_groups.items())
    ]
    trend_value = dict(trend or {})
    trend_value["aggregation"] = "multi_scope_mean"
    trend_value["significance_status"] = "missing" if trend_value.get("p_value") is None else "available"
    if trend_value.get("p_value") is None:
        trend_value["significance_interpretation"] = "not_assessed"
    return {
        "target_year": target,
        "current": {
            "risk_level": selected.get("risk_level") if selected else None,
            "risk_level_code": selected.get("risk_level_code") if selected else None,
            "values": {field: selected.get("metrics", {}).get(field) for field in RISK_FIELDS} if selected else {field: None for field in RISK_FIELDS},
        },
        "source_scope": source_scope,
        "fallback": bool(source_scope and source_scope != ordered_scopes[0]),
        "quality_flags": (
            (["core_scope_missing_fallback"] if source_scope and source_scope != ordered_scopes[0] else [])
            + (["no_valid_target_ceri"] if selected is None else [])
            + (["invalid_p_value_treated_as_missing"] if trend_value.get("p_value_invalid") else [])
        ),
        "trend": trend_value,
        "annual_series": annual_series,
        "spatial_gradient": gradient,
        "source_scope_order": list(ordered_scopes),
    }


def _selected_scope(
    records: list[dict[str, Any]], field: str, preferred: tuple[str, ...]
) -> str | None:
    for scope in preferred:
        count = sum(
            item["metrics"].get(field) is not None
            for item in records
            if item["scope"] == scope
        )
        if count >= MINIMUM_VALID_YEARS:
            return scope
    return None


def build_snapshot_observations(
    snapshot: dict[str, Any],
) -> tuple[list[Observation], dict[str, Any]]:
    main_records = snapshot["main_records"]
    total_landscape = [
        item
        for item in snapshot["landscape_records"]
        if item["category"] == "Total_Landscape"
    ]
    main_selection = {
        destination: _selected_scope(main_records, source, ("覆盖区", "0-1km"))
        for source, destination in MAIN_FIELD_MAP.items()
    }
    preferred_landscape = (
        ("0-1km",)
        if snapshot["project_type"] == "wind"
        else ("本体", "0-1km")
    )
    landscape_selection = {
        field: _selected_scope(total_landscape, field, preferred_landscape)
        for field in LANDSCAPE_DIAGNOSIS_FIELDS
    }
    years = sorted(
        year for year in snapshot["source_years"] if year <= snapshot["target_year"]
    )
    observations: list[Observation] = []
    for year in years:
        values: dict[str, Any] = {
            "PROJECT_TYPE": snapshot["project_type"],
            "FID": snapshot["fid"],
            "YEAR": year,
            "RISK_LEVEL": "",
            "RISK_LEVEL_CODE": None,
            "RISK_TYPES": "",
            "MEASURE_TEXT": "",
        }
        source_rows: list[int] = []
        for source, destination in MAIN_FIELD_MAP.items():
            scope = main_selection[destination]
            match = next(
                (
                    item
                    for item in main_records
                    if item["year"] == year and item["scope"] == scope
                ),
                None,
            )
            values[destination] = None if match is None else match["metrics"].get(source)
            if match is not None:
                source_rows.append(int(match["source_row"]))
        for field in LANDSCAPE_DIAGNOSIS_FIELDS:
            scope = landscape_selection[field]
            match = next(
                (
                    item
                    for item in total_landscape
                    if item["year"] == year and item["scope"] == scope
                ),
                None,
            )
            values[field] = None if match is None else match["metrics"].get(field)
            if match is not None:
                source_rows.append(int(match["source_row"]))
        observations.append(
            Observation(
                project_type=ProjectType(snapshot["project_type"]),
                fid=int(snapshot["fid"]),
                year=year,
                values=values,
                source_row=min(source_rows, default=0),
            )
        )

    def scope_series(
        records: list[dict[str, Any]], fields: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return [
            {
                "year": item["year"],
                "scope": item["scope"],
                "category": item.get("category"),
                "values": {name: item["metrics"].get(name) for name in fields},
                "source_sheet": item["source_sheet"],
                "source_row": item["source_row"],
            }
            for item in records
        ]

    landscape_classes = [
        item for item in snapshot["landscape_records"] if item["category"] != "Total_Landscape"
    ]
    landscape_class_series = scope_series(
        landscape_classes, ("CA", *LANDSCAPE_DIAGNOSIS_FIELDS)
    )
    for item in landscape_class_series:
        item["class_code"] = item.get("category")
        item["class_name"] = LANDSCAPE_CLASS_MAP.get(
            str(item.get("category")), "类别未提供"
        )
    spatial = {
        "main_indicator_scope_selection": main_selection,
        "landscape_indicator_scope_selection": landscape_selection,
        "main_scope_series": scope_series(main_records, tuple(MAIN_FIELD_MAP)),
        "landscape_total_series": scope_series(
            total_landscape, ("CA", *LANDSCAPE_DIAGNOSIS_FIELDS)
        ),
        "landscape_class_series": landscape_class_series,
        "landscape_class_semantics": "mapped_cls_v1",
        "landscape_class_mapping": LANDSCAPE_CLASS_MAP,
        "protected_area_status": snapshot["protected_area_status"],
        "protected_area_records": snapshot["protected_area_records"],
        "absence_interpretation": "not_recorded",
    }
    risk_evidence = _build_risk_evidence(snapshot)
    if risk_evidence is not None:
        spatial["risk_evidence"] = risk_evidence
        spatial["region"] = snapshot.get("region", {})
    return observations, spatial


def build_report_ir_from_snapshot(
    snapshot: dict[str, Any],
    *,
    generated_date: str | None = None,
    prepared_by: str = "生态状况评估编制组",
    analysis_backend: Any = None,
) -> dict[str, Any]:
    series, spatial = build_snapshot_observations(snapshot)
    facts = build_indicator_facts(series)
    rules = load_json_config("problem_rules.json")
    scope = DataScope(has_buffer_gradient=True)
    diagnoses = build_problem_diagnoses(
        snapshot["project_type"], [], facts, scope, rules
    )
    analysis_config = load_json_config("analysis_config.json")
    project = {
        "fid": int(snapshot["fid"]),
        "name": snapshot["project_name"],
        "project_type": snapshot["project_type"],
        "project_attributes": asdict(scope),
    }
    matching = snapshot.get("matching_result") or {}
    measure_text = str(matching.get("measure_text") or "").strip()
    measures, measure_audit = build_measure_recommendations(
        measure_text, snapshot["project_type"], scope, diagnoses
    )
    diagnoses, cross_problem, backend_name = build_analysis_material(
        project, diagnoses, facts, measures, rules, analysis_config, analysis_backend
    )
    available = [item["indicator"] for item in facts if item["valid"]]
    missing = [item["indicator"] for item in facts if not item["valid"]]
    risk_evidence = spatial.get("risk_evidence")
    report = {
        "schema_version": "2.5-mvp" if risk_evidence is not None else "2.4-mvp",
        "report_metadata": {
            "report_id": f"eco-{snapshot['project_type']}-{snapshot['fid']}-{snapshot['target_year']}",
            "project_key": snapshot["project_key"],
            "report_title": "生态状况智能诊断与修复决策报告",
            "current_year": int(snapshot["target_year"]),
            "evaluation_start_year": series[0].year,
            "evaluation_end_year": int(snapshot["target_year"]),
            "generated_date": generated_date or date.today().isoformat(),
            "prepared_by": prepared_by,
            "problem_rules": value_sha256(load_json_config("problem_rules.json")),
            "analysis_config": value_sha256(load_json_config("analysis_config.json")),
            "analysis_prompts": value_sha256(
                [PROBLEM_ANALYSIS_PROMPT, CROSS_ANALYSIS_PROMPT]
            ),
        },
        "project": project,
        "region": snapshot.get("region", {}),
        "data_profile": {
            "available_indicators": available,
            "missing_indicators": missing,
            "known_limitations": sorted(
                {value for item in diagnoses for value in item["limitations"]}
            ),
        },
        "indicator_facts": facts,
        "problem_diagnoses": diagnoses,
        "overall_assessment": {
            "risk_level": (
                (risk_evidence or {}).get("current", {}).get("risk_level")
                if risk_evidence is not None
                else matching.get("risk_level")
            ),
            "risk_level_code": (
                (risk_evidence or {}).get("current", {}).get("risk_level_code")
                if risk_evidence is not None
                else matching.get("risk_level_code")
            ),
            "risk_level_status": "provided" if (
                ((risk_evidence or {}).get("current", {}).get("risk_level")
                 if risk_evidence is not None else matching.get("risk_level"))
            ) else "not_provided",
            "reported_risk_types": [matching["risk_types"]] if matching.get("risk_types") else [],
        },
        "cross_problem_analysis": cross_problem,
        "measure_recommendations": measures,
        "spatial_evidence": spatial,
        "report_assets": {"charts": [], "tables": []},
        "audit": {
            "source_workbook_sha256": snapshot["source_workbook_sha256"],
            "source_snapshot_sha256": snapshot["snapshot_sha256"],
            "source_years": [item.year for item in series],
            "source_scope_selection": {
                "main": spatial["main_indicator_scope_selection"],
                "landscape": spatial["landscape_indicator_scope_selection"],
            },
            "analysis_backend": backend_name,
            "analysis_fallback_problem_ids": list(
                getattr(analysis_backend, "fallback_problem_ids", [])
            ),
            "analysis_cross_fallback": bool(
                getattr(analysis_backend, "cross_fallback", False)
            ),
            "analysis_request_count": int(
                getattr(analysis_backend, "request_count", 0)
            ),
            "analysis_logical_request_count": int(
                getattr(analysis_backend, "logical_request_count", 0)
            ),
            "analysis_retry_count": int(
                getattr(analysis_backend, "retry_count", 0)
            ),
            "analysis_request_durations_seconds": list(
                getattr(analysis_backend, "request_durations_seconds", [])
            ),
            "priority_module_enabled": False,
            "matching_result_source": {
                key: matching.get(key)
                for key in ("source_workbook_sha256", "source_sheet", "source_row", "target_year")
            } if matching else None,
            "measure_input_status": "provided" if measure_text else "not_provided",
            "measure_filter": measure_audit,
        },
    }
    if risk_evidence is not None:
        report["risk_evidence"] = risk_evidence
    errors = validate_report_ir_mvp(report)
    if errors:
        raise ValueError("Invalid ReportIR: " + ", ".join(errors))
    return report


@contextmanager
def _render_slot(render_semaphore: Any) -> Iterator[None]:
    if render_semaphore is not None:
        render_semaphore.acquire()
    try:
        yield
    finally:
        if render_semaphore is not None:
            render_semaphore.release()

def _writer_from_dict(value: dict[str, Any]) -> WriterResult:
    return WriterResult(
        markdown=value["markdown"],
        backend=value["backend"],
        fallback_reason=value.get("fallback_reason"),
        draft=value.get("draft"),
        rejected_draft=value.get("rejected_draft"),
        boundary_repaired=bool(value.get("boundary_repaired", False)),
    )


def _chart_file_hashes(charts: list[dict[str, Any]]) -> dict[str, str] | None:
    hashes: dict[str, str] = {}
    for chart in charts:
        for field in ("path", "vector_path", "pdf_path"):
            raw_path = chart.get(field)
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_file():
                return None
            hashes[str(path)] = file_sha256(path)
    return hashes


def _analysis_snapshot_hash(snapshot: dict[str, Any]) -> str:
    value = snapshot.get("analysis_snapshot_sha256")
    if isinstance(value, str) and value:
        return value
    return value_sha256(
        {
            key: item
            for key, item in snapshot.items()
            if key not in {"project_name", "snapshot_sha256", "analysis_snapshot_sha256"}
        }
    )


def build_snapshot_bundle(
    snapshot_path: str | Path,
    output_directory: str | Path,
    *,
    writer_base_url: str,
    writer_model: str,
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY",
    generated_date: str | None = None,
    prepared_by: str = "生态状况评估编制组",
    resume: bool = False,
    render_semaphore: Any = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    snapshot = read_json(snapshot_path)
    output = Path(output_directory)
    checkpoints = output / "checkpoints"
    result = output / "result"
    output.mkdir(parents=True, exist_ok=True)
    checkpoints.mkdir(parents=True, exist_ok=True)
    result.mkdir(parents=True, exist_ok=True)

    report_fingerprint = value_sha256(
        {
            "stage": "report_ir_2.5",
            "analysis_snapshot": _analysis_snapshot_hash(snapshot),
            "model": writer_model,
            "base_url": writer_base_url,
            "generated_date": generated_date,
            "prepared_by": prepared_by,
            "trend_config": value_sha256(load_json_config("trend_config.json")),
            "problem_rules": value_sha256(load_json_config("problem_rules.json")),
            "analysis_config": value_sha256(load_json_config("analysis_config.json")),
            "problem_prompt": value_sha256(PROBLEM_ANALYSIS_PROMPT),
            "cross_prompt": value_sha256(CROSS_ANALYSIS_PROMPT),
        }
    )
    report = load_checkpoint(checkpoints / "report_ir.json", report_fingerprint) if resume else None
    analysis_backend = OpenAICompatibleAnalysisBackend(
        OpenAICompatibleAnalysisConfig(
            base_url=writer_base_url,
            model=writer_model,
            api_key_env=api_key_env,
            checkpoint_directory=checkpoints / "analysis",
        )
    )
    if report is None:
        report = build_report_ir_from_snapshot(
            snapshot,
            generated_date=generated_date,
            prepared_by=prepared_by,
            analysis_backend=analysis_backend,
        )
        save_checkpoint(
            checkpoints / "report_ir.json",
            stage="report_ir",
            fingerprint=report_fingerprint,
            value=report,
        )
    else:
        report["project"]["name"] = snapshot["project_name"]
        report["audit"]["source_snapshot_sha256"] = snapshot["snapshot_sha256"]
        save_checkpoint(
            checkpoints / "report_ir.json",
            stage="report_ir",
            fingerprint=report_fingerprint,
            value=report,
        )

    chart_fingerprint = value_sha256(
        {
            "stage": "charts-v2.5",
            "indicator_facts": report["indicator_facts"],
            "problem_diagnoses": report["problem_diagnoses"],
            "evaluation_start_year": report["report_metadata"]["evaluation_start_year"],
            "evaluation_end_year": report["report_metadata"]["evaluation_end_year"],
        }
    )
    chart_value = (
        load_checkpoint(checkpoints / "charts.json", chart_fingerprint)
        if resume
        else None
    )
    charts: list[dict[str, Any]] | None = None
    if isinstance(chart_value, dict) and isinstance(chart_value.get("charts"), list):
        candidate = chart_value["charts"]
        current_hashes = _chart_file_hashes(candidate)
        if current_hashes is not None and current_hashes == chart_value.get("files"):
            charts = candidate
    elif isinstance(chart_value, list):
        current_hashes = _chart_file_hashes(chart_value)
        if current_hashes is not None:
            charts = chart_value
            save_checkpoint(
                checkpoints / "charts.json",
                stage="charts",
                fingerprint=chart_fingerprint,
                value={"charts": charts, "files": current_hashes},
            )
    if charts is None:
        with _render_slot(render_semaphore):
            charts = build_report_charts(report, result / "charts")
        chart_files = _chart_file_hashes(charts)
        if chart_files is None:
            raise ValueError("chart rendering did not produce all declared files")
        save_checkpoint(
            checkpoints / "charts.json",
            stage="charts",
            fingerprint=chart_fingerprint,
            value={"charts": charts, "files": chart_files},
        )
    report["report_assets"]["charts"] = charts

    writer_fingerprint = value_sha256(
        {
            "stage": "writer-v2.5-strict-llm-3",
            "report": value_sha256(report),
            "model": writer_model,
            "base_url": writer_base_url,
            "writer_prompt": value_sha256(SYSTEM_PROMPT),
        }
    )
    writer_value = (
        load_checkpoint(checkpoints / "writer_result.json", writer_fingerprint)
        if resume
        else None
    )
    writer_backend = OpenAICompatibleNarrativeBackend(
        OpenAICompatibleWriterConfig(
            base_url=writer_base_url,
            model=writer_model,
            api_key_env=api_key_env,
            checkpoint_directory=checkpoints / "writer",
        )
    )
    if writer_value is None:
        writer = write_report(report, writer_backend)
        if writer.backend != "llm" or writer.fallback_reason:
            atomic_write_json(
                checkpoints / "writer_failure.json",
                {
                    "stage": "writer",
                    "status": "failed",
                    "backend": writer.backend,
                    "fallback_reason": writer.fallback_reason,
                    "logical_request_count": int(writer_backend.logical_request_count),
                    "retry_count": int(writer_backend.retry_count),
                    "fingerprint": writer_fingerprint,
                    "failed_epoch": time.time(),
                },
            )
            raise RuntimeError(
                "batch Writer did not produce validated LLM output: "
                + str(writer.fallback_reason or writer.backend)
            )
        writer_value = asdict(writer)
        writer_value["_llm_stats"] = {
            "logical_request_count": int(writer_backend.logical_request_count),
            "retry_count": int(writer_backend.retry_count),
        }
        save_checkpoint(
            checkpoints / "writer_result.json",
            stage="writer",
            fingerprint=writer_fingerprint,
            value=writer_value,
        )
    else:
        writer = _writer_from_dict(writer_value)
    writer_stats = writer_value.get("_llm_stats", {})

    atomic_write_json(result / "report_ir.json", report)
    atomic_write_text(result / "report.md", writer.markdown)
    render_fingerprint = value_sha256(
        {
            "stage": "documents-v2.5",
            "markdown": value_sha256(writer.markdown),
            "project_name": report["project"]["name"],
            "year": report["report_metadata"]["current_year"],
            "style": value_sha256(load_json_config("report_style_config.json")),
        }
    )
    render_value = (
        load_checkpoint(checkpoints / "documents.json", render_fingerprint)
        if resume
        else None
    )
    if isinstance(render_value, dict):
        docx_path = result / "report.docx"
        pdf_path = result / "report.pdf"
        if (
            not docx_path.is_file()
            or not pdf_path.is_file()
            or file_sha256(docx_path) != render_value.get("docx_sha256")
            or file_sha256(pdf_path) != render_value.get("pdf_sha256")
        ):
            render_value = None
    if render_value is None:
        with _render_slot(render_semaphore):
            docx = render_docx_bytes(
                writer.markdown,
                report["project"]["name"],
                report["report_metadata"]["current_year"],
            )
            atomic_write_bytes(result / "report.docx", docx)
            temporary_pdf = result / ".report.pdf.tmp"
            write_pdf_markdown(
                writer.markdown,
                report["project"]["name"],
                report["report_metadata"]["current_year"],
                temporary_pdf,
            )
            os.replace(temporary_pdf, result / "report.pdf")
        render_value = {
            "docx_sha256": file_sha256(result / "report.docx"),
            "pdf_sha256": file_sha256(result / "report.pdf"),
        }
        save_checkpoint(
            checkpoints / "documents.json",
            stage="documents",
            fingerprint=render_fingerprint,
            value=render_value,
        )
    else:
        docx = (result / "report.docx").read_bytes()

    analysis_logical_requests = int(
        report["audit"].get("analysis_logical_request_count", 0)
    )
    analysis_retries = int(report["audit"].get("analysis_retry_count", 0))
    writer_logical_requests = int(writer_stats.get("logical_request_count", 0))
    writer_retries = int(writer_stats.get("retry_count", 0))
    logical_requests = analysis_logical_requests + writer_logical_requests
    retry_attempts = analysis_retries + writer_retries
    validation = {
        "valid": True,
        "report_ir_issues": validate_report_ir_mvp(report),
        "writer_issues": validate_writer_output(writer.markdown, report),
        "docx_structural_issues": list(validate_docx_bytes(docx)),
        "analysis_backend": report["audit"]["analysis_backend"],
        "analysis_fallback_problem_ids": report["audit"][
            "analysis_fallback_problem_ids"
        ],
        "analysis_cross_fallback": report["audit"]["analysis_cross_fallback"],
        "writer_backend": writer.backend,
        "writer_boundary_repaired": writer.boundary_repaired,
        "writer_fallback_reason": writer.fallback_reason,
        "llm_logical_requests": logical_requests,
        "llm_retry_attempts": retry_attempts,
        "api_retry_rate": (
            retry_attempts / (logical_requests + retry_attempts)
            if logical_requests + retry_attempts
            else 0.0
        ),
        "writer_quality_warnings": (
            narrative_quality_warnings(writer.draft, report)
            if writer.draft is not None
            else []
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    validation["valid"] = not any(
        validation[name]
        for name in ("report_ir_issues", "writer_issues", "docx_structural_issues")
    )
    atomic_write_json(result / "validation_report.json", validation)
    files = {}
    for path in sorted(result.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files[path.relative_to(result).as_posix()] = {
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
            }
    manifest = {
        "format_version": 6,
        "project_key": snapshot["project_key"],
        "project_type": snapshot["project_type"],
        "fid": snapshot["fid"],
        "target_year": snapshot["target_year"],
        "validation": validation,
        "files": files,
    }
    atomic_write_json(result / "manifest.json", manifest)
    if not validation["valid"]:
        raise ValueError(
            "snapshot report validation failed: "
            + json.dumps(validation, ensure_ascii=False)
        )
    return manifest
