from __future__ import annotations

from pathlib import Path

from .diagnosis import build_diagnoses
from .evidence import build_problem_evidence, split_risk_types
from .ingest import InputValidationError, load_project_series
from .measures import filter_measures
from .metrics import TREND_FIELDS, summarize_trend
from .models import DataScope, ProjectType, ReportIR
from .strategy import select_measures
from .validation import collect_input_warnings, metric_snapshot, validate_report_ir


def build_report_ir(
    workbook_path: str | Path,
    project_type: ProjectType | str,
    fid: int,
    target_year: int | None = None,
    project_name: str | None = None,
    data_scope: DataScope | None = None,
) -> ReportIR:
    project = ProjectType(project_type)
    series = load_project_series(workbook_path, project, fid)
    target = series[-1]
    if target_year is not None:
        matches = [item for item in series if item.year == int(target_year)]
        if not matches:
            raise InputValidationError(
                f"No record found for project={project.value}, FID={fid}, YEAR={target_year}"
            )
        target = matches[0]
    analysis_series = [item for item in series if item.year <= target.year]
    trends = [
        trend
        for field_name in TREND_FIELDS
        if (trend := summarize_trend(analysis_series, field_name)) is not None
    ]
    scope = data_scope or DataScope()
    reported_risk_types = split_risk_types(target.text("RISK_TYPES"))
    measure_audit = filter_measures(target.text("MEASURE_TEXT"), project, scope)
    risk_code_value = target.number("RISK_LEVEL_CODE")
    diagnoses = build_diagnoses(project, reported_risk_types, trends, scope)
    selected_measures = select_measures(diagnoses, measure_audit)
    report = ReportIR(
        schema_version="0.1.0",
        project_type=project.value,
        fid=int(fid),
        target_year=target.year,
        source_sheet=project.value,
        source_row=target.source_row,
        project_name=project_name,
        years=[item.year for item in analysis_series],
        latest_metrics=metric_snapshot(target),
        trends=trends,
        risk_level=target.text("RISK_LEVEL"),
        risk_level_code=None if risk_code_value is None else int(risk_code_value),
        reported_risk_types=reported_risk_types,
        problems=build_problem_evidence(reported_risk_types, trends),
        diagnoses=diagnoses,
        data_scope=scope,
        accepted_measures=[item.text for item in measure_audit if item.accepted],
        selected_measures=selected_measures,
        measure_audit=measure_audit,
        warnings=collect_input_warnings(target),
    )
    supported_risks = {problem.problem for problem in report.problems}
    report.warnings.extend(
        f"unsupported_reported_risk_type:{risk}"
        for risk in reported_risk_types
        if risk not in supported_risks
    )
    diagnosed_with_no_measure = {
        diagnosis.problem
        for diagnosis in diagnoses
        if diagnosis.status in {"突出问题", "存在但改善中", "存在风险"}
    }.difference(item.problem for item in selected_measures)
    report.warnings.extend(
        f"no_relevant_candidate_measure:{problem}"
        for problem in sorted(diagnosed_with_no_measure)
    )
    validation_errors = validate_report_ir(report)
    if validation_errors:
        raise InputValidationError("Invalid ReportIR: " + ", ".join(validation_errors))
    return report
