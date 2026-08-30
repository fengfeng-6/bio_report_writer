from __future__ import annotations

import math

from .models import Observation, ReportIR


LATEST_METRIC_FIELDS = (
    "MEAN_NDVI",
    "STD_NDVI",
    "MEAN_NPP",
    "STD_NPP",
    "CA",
    "LPI",
    "NP",
    "PD",
    "ED",
    "LSI",
    "MEAN_VCI",
    "MEAN_EVI",
    "MEAN_SIF",
    "MEAN_VCS_C",
    "MEAN_VCS_CO2e",
    "ERI",
    "LER",
    "ES",
    "EP",
    "RER",
    "CVR",
)


def metric_snapshot(observation: Observation) -> dict[str, float | int | str | None]:
    return {field: observation.values.get(field) for field in LATEST_METRIC_FIELDS}


def collect_input_warnings(observation: Observation) -> list[str]:
    warnings: list[str] = []
    for field_name in LATEST_METRIC_FIELDS:
        value = observation.values.get(field_name)
        if value is None or value == "":
            warnings.append(f"missing_latest_metric:{field_name}")
        elif isinstance(value, float) and not math.isfinite(value):
            warnings.append(f"non_finite_latest_metric:{field_name}")
    if observation.text("ML_ENABLED") in {"是", "true", "True", "1"}:
        warnings.append("ml_recommendation_present:requires_rule_review")
    return warnings


def validate_report_ir(report: ReportIR) -> list[str]:
    errors: list[str] = []
    if report.target_year not in report.years:
        errors.append("target_year_not_in_series")
    if not report.risk_level:
        errors.append("missing_risk_level")
    accepted = set(report.accepted_measures)
    rejected = {
        item.text
        for item in report.measure_audit
        if not item.accepted and item.reason != "duplicate"
    }
    overlap = sorted(accepted.intersection(rejected))
    if overlap:
        errors.append(f"accepted_rejected_overlap:{overlap}")
    return errors
