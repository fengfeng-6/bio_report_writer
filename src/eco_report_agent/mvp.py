from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from .analysis_builder import build_analysis_material
from .evidence import split_risk_types
from .ingest import InputValidationError, load_project_series
from .measures import filter_measures
from .metrics import TREND_FIELDS, linear_slope
from .models import DataScope, Observation, ProjectType


CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


def load_json_config(name: str) -> Any:
    return json.loads((CONFIG_ROOT / name).read_text(encoding="utf-8"))


def _relative_change(new: float, old: float, epsilon: float) -> float:
    return (new - old) / max(abs(old), epsilon)


def _smoothed(values: list[float]) -> list[float]:
    if len(values) < 3:
        return values[:]
    return [values[0], *[median(values[index - 1:index + 2]) for index in range(1, len(values) - 1)], values[-1]]


def _piecewise_pattern(years: list[int], values: list[float], threshold: float, epsilon: float) -> str | None:
    if len(values) < 4:
        return None
    best: tuple[float, str] | None = None
    for split in range(2, len(values) - 1):
        left = values[:split]
        right = values[split - 1:]
        left_slope = linear_slope(zip(map(float, years[:split]), left))
        right_slope = linear_slope(zip(map(float, years[split - 1:]), right))
        left_change = left_slope * (len(left) - 1) / max(abs(sum(left) / len(left)), epsilon)
        right_change = right_slope * (len(right) - 1) / max(abs(sum(right) / len(right)), epsilon)
        if abs(left_change) < threshold or abs(right_change) < threshold or left_change * right_change >= 0:
            continue
        prediction = []
        for xs, ys, slope in ((years[:split], left, left_slope), (years[split - 1:], right, right_slope)):
            intercept = sum(ys) / len(ys) - slope * (sum(xs) / len(xs))
            prediction.extend((value - (intercept + slope * year)) ** 2 for year, value in zip(xs, ys))
        pattern = "rise_then_fall" if left_change > 0 else "fall_then_rise"
        candidate = (sum(prediction), pattern)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return None if best is None else best[1]


def build_indicator_fact(series: list[Observation], indicator: str, config: dict[str, Any]) -> dict[str, Any]:
    points = [(item.year, item.number(indicator), item.source_row) for item in series]
    valid = [(year, value, source_row) for year, value, source_row in points if value is not None and math.isfinite(value)]
    years = [year for year, _, _ in valid]
    values = [float(value) for _, value, _ in valid]
    missing = [year for year, value, _ in points if value is None]
    minimum_years = int(config["minimum_valid_years"])
    fact: dict[str, Any] = {
        "fact_id": f"IF-{indicator}",
        "indicator": indicator,
        "valid": len(valid) >= minimum_years,
        "time_series": [
            {
                "year": year,
                "value": value,
                "source_ref": {"row": source_row, "field": indicator},
            }
            for year, value, source_row in valid
        ],
        "statistics": {},
        "trend": {"long_term": "unknown", "recent": "unknown", "turning_points": []},
        "quality": {"valid_year_count": len(valid), "missing_years": missing, "sufficiency": "sufficient" if len(valid) >= minimum_years else "insufficient"},
    }
    if not valid:
        return fact
    minimum_index = min(range(len(values)), key=values.__getitem__)
    maximum_index = max(range(len(values)), key=values.__getitem__)
    fact["statistics"] = {
        "current_year": years[-1], "current_value": values[-1],
        "minimum": {"year": years[minimum_index], "value": values[minimum_index]},
        "maximum": {"year": years[maximum_index], "value": values[maximum_index]},
    }
    if len(valid) < minimum_years:
        return fact
    epsilon = float(config["epsilon"])
    threshold = float(config["long_term_relative_change_threshold"])
    stable_threshold = float(config["stable_relative_range_threshold"])
    recent_threshold = float(config["recent_relative_change_threshold"])
    smooth = _smoothed(values)
    slope = linear_slope(zip(map(float, years), values))
    mean_value = sum(values) / len(values)
    relative_slope = slope * (len(values) - 1) / max(abs(mean_value), epsilon)
    relative_range = (max(values) - min(values)) / max(abs(mean_value), epsilon)
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    tolerance = max(abs(mean_value), epsilon) * 0.001
    signs = [1 if value > tolerance else -1 if value < -tolerance else 0 for value in deltas]
    nonzero = [value for value in signs if value]
    switches = sum(nonzero[index] != nonzero[index - 1] for index in range(1, len(nonzero)))
    piecewise = _piecewise_pattern(years, smooth, threshold, epsilon)
    if abs(relative_slope) < threshold and relative_range < stable_threshold:
        long_term = "stable"
    elif piecewise:
        long_term = piecewise
    elif relative_slope >= threshold:
        long_term = "fluctuating_upward" if switches >= 2 else "overall_up"
    elif relative_slope <= -threshold:
        long_term = "fluctuating_downward" if switches >= 2 else "overall_down"
    else:
        long_term = "stable"
    recent_change = _relative_change(values[-1], values[-2], epsilon)
    recent = "up" if recent_change >= recent_threshold else "down" if recent_change <= -recent_threshold else "stable"
    turning_points = [years[index] for index in range(1, len(signs)) if signs[index] and signs[index - 1] and signs[index] != signs[index - 1]]
    fact["trend"] = {"long_term": long_term, "recent": recent, "turning_points": turning_points, "relative_slope": relative_slope, "relative_range": relative_range, "recent_relative_change": recent_change}
    return fact


def build_indicator_facts(series: list[Observation], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = config or load_json_config("trend_config.json")
    return [build_indicator_fact(series, indicator, settings) for indicator in TREND_FIELDS if any(item.number(indicator) is not None for item in series)]


def _requirement_satisfied(requirement: dict[str, Any], scope: dict[str, Any]) -> bool:
    if requirement.get("operator") != "equals":
        raise ValueError(f"Unsupported data requirement operator: {requirement.get('operator')}")
    return scope.get(requirement["field"]) == requirement.get("value")


def _canonical_direction(pattern: str, recent: str) -> str:
    if pattern in {"overall_up", "fluctuating_upward"}:
        return "up"
    if pattern in {"overall_down", "fluctuating_downward"}:
        return "down"
    if pattern == "rise_then_fall":
        return "down" if recent != "up" else "mixed"
    if pattern == "fall_then_rise":
        return "up" if recent != "down" else "mixed"
    return "stable" if pattern == "stable" else "unknown"


def build_problem_diagnoses(project_type: str, risk_types: list[str], facts: list[dict[str, Any]], scope: DataScope, rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    fact_map = {item["indicator"]: item for item in facts}
    scope_map = asdict(scope)
    risk_set = set(risk_types)
    diagnoses: list[dict[str, Any]] = []
    for rule in rules or load_json_config("problem_rules.json"):
        problem_id = rule["problem_id"]
        if project_type not in rule["applicable_project_types"]:
            diagnoses.append(_diagnosis_record(rule, "not_applicable", "unknown", "unknown", "not_assessable", "insufficient", "none", [], ["项目类型不适用"], "不适用"))
            continue
        failed_requirements = [item["field"] for item in rule["data_requirements"] if not _requirement_satisfied(item, scope_map)]
        missing = [name for name in rule["required_indicators"] if name not in fact_map or not fact_map[name]["valid"]]
        if failed_requirements or missing:
            limitations = [*(f"数据条件未满足：{name}" for name in failed_requirements), *(f"指标数据不足：{name}" for name in missing)]
            diagnoses.append(_diagnosis_record(rule, "unknown", "unknown", "unknown", "not_assessable", "insufficient", "none", [], limitations, "数据不足"))
            continue
        evidence = []
        support: list[bool | None] = []
        recent_effects: list[str] = []
        for indicator in rule["required_indicators"]:
            fact = fact_map[indicator]
            direction = _canonical_direction(fact["trend"]["long_term"], fact["trend"]["recent"])
            adverse = rule["indicator_conditions"][indicator]
            item_support = direction == adverse if direction in {"up", "down"} else None if direction == "unknown" else False
            support.append(item_support)
            recent = fact["trend"]["recent"]
            recent_effects.append("adverse" if recent == adverse else "improving" if recent in {"up", "down"} else "stable")
            evidence.append({"evidence_id": f"EV-{problem_id}-{indicator}", "indicator": indicator, "fact_ref": f"IF-{indicator}", "adverse_direction": adverse, "observed_direction": direction, "supports_problem": item_support})
        supported = sum(value is True for value in support)
        opposed = sum(value is False for value in support)
        total = len(support)
        all_support = supported == total
        majority_support = supported > total / 2
        any_support = supported > 0
        logic = rule["combine_logic"]
        confirmed = all_support if logic == "all" else any_support if logic == "any" else majority_support
        risk_tag = rule["problem_name"] in risk_set or bool(risk_set.intersection(rule["risk_type_aliases"]))
        if supported and opposed:
            consistency = "conflicting" if supported == opposed else "partially_consistent"
        elif all_support:
            consistency = "consistent"
        else:
            consistency = "partially_consistent" if supported else "consistent"
        if confirmed:
            existence, severity, confidence, level = "confirmed", "high" if all_support else "medium", "high" if all_support else "medium", "A"
        elif any_support or risk_tag:
            existence, severity, confidence, level = "possible", "medium", "medium" if any_support else "low", "A" if any_support else "B"
        else:
            existence, severity, confidence, level = "not_observed", "low", "high" if consistency == "consistent" else "medium", "A"
        adverse_recent = recent_effects.count("adverse")
        improving_recent = recent_effects.count("improving")
        if adverse_recent and improving_recent:
            problem_trend = "mixed"
        elif adverse_recent:
            problem_trend = "recently_worsening" if existence != "confirmed" else "worsening"
        elif improving_recent:
            problem_trend = "recently_improving" if existence in {"confirmed", "possible"} else "improving"
        else:
            problem_trend = "stable"
        status = "突出问题" if existence == "confirmed" and severity == "high" else "存在但改善中" if existence in {"confirmed", "possible"} and problem_trend in {"improving", "recently_improving"} else "存在风险" if existence == "possible" else "未见明显恶化"
        diagnoses.append(_diagnosis_record(rule, existence, severity, problem_trend, confidence, consistency, level, evidence, [], status))
    return diagnoses


def _diagnosis_record(rule: dict[str, Any], existence: str, severity: str, trend: str, confidence: str, consistency: str, evidence_level: str, evidence: list[dict[str, Any]], limitations: list[str], status: str) -> dict[str, Any]:
    problem_id = rule["problem_id"]
    return {
        "problem_id": problem_id,
        "problem_name": rule["problem_name"],
        "applicability": "not_applicable" if status == "不适用" else "applicable",
        "data_sufficiency": "insufficient" if status == "数据不足" else "sufficient",
        "status": status,
        "existence": existence,
        "severity": severity,
        "trend": trend,
        "confidence": confidence,
        "evidence_consistency": consistency,
        "evidence_level": evidence_level,
        "supporting_indicators": rule["required_indicators"],
        "evidence": evidence,
        "limitations": limitations,
        "provenance": {
            "rule_id": f"PR-{problem_id}",
            "rule_version": "2.1-mvp",
            "rule_source": "config/problem_rules.json",
        },
    }


def build_measure_recommendations(
    raw: str,
    project_type: str,
    scope: DataScope,
    diagnoses: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match compliant measures and group them by reportable problem, without priority scoring."""
    candidate_audit = filter_measures(raw, ProjectType(project_type), scope)
    candidates = {item.text: item for item in candidate_audit if item.accepted}
    knowledge = load_json_config("measure_knowledge.json")
    reportable = [
        item["problem_id"] for item in diagnoses
        if item["status"] not in {"数据不足", "不适用"}
    ]
    problem_order = {problem_id: index for index, problem_id in enumerate(reportable)}
    recommendations: list[dict[str, Any]] = []
    eligible = [
        item
        for item in knowledge
        if item["name"] in candidates and project_type in item["applicable_project_types"]
    ]
    for item in eligible:
        targets = [
            problem_id
            for problem_id in item["target_problem_ids"]
            if problem_id in problem_order
        ]
        if not targets:
            continue
        targets.sort(key=problem_order.__getitem__)
        recommendations.append({
            "measure_id": item["measure_id"],
            "name": item["name"],
            "target_problem_ids": targets,
            "mechanism": item["mechanism"],
            "parameter_status": "not_provided",
            "implementation_parameters": None,
            "source_record": candidates[item["name"]].source,
            "knowledge_ref": {"config": "config/measure_knowledge.json", "measure_id": item["measure_id"]},
        })
    recommendations.sort(key=lambda item: (problem_order[item["target_problem_ids"][0]], item["measure_id"]))
    audit = [asdict(item) for item in candidate_audit]
    known_names = {item["name"] for item in knowledge}
    audit.extend({"text": name, "accepted": False, "reason": "not_in_measure_knowledge", "source": candidates[name].source} for name in sorted(set(candidates) - known_names))
    return recommendations, audit

def validate_json_schema_subset(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") == "object" and not isinstance(instance, dict):
        return ["schema_type:root"]
    errors.extend(f"schema_required:{name}" for name in schema.get("required", []) if name not in instance)
    if schema.get("additionalProperties") is False:
        errors.extend(f"schema_additional:{name}" for name in sorted(set(instance) - set(schema.get("properties", {}))))
    for name, rule in schema.get("properties", {}).items():
        if name in instance and "const" in rule and instance[name] != rule["const"]:
            errors.append(f"schema_const:{name}")
        if name in instance and "enum" in rule and instance[name] not in rule["enum"]:
            errors.append(f"schema_enum:{name}")
        if name in instance and rule.get("type") == "array" and not isinstance(instance[name], list):
            errors.append(f"schema_type:{name}")
    return errors


def validate_report_ir_mvp(report: dict[str, Any]) -> list[str]:
    schema = load_json_config("reportir_schema.json")
    errors: list[str] = validate_json_schema_subset(report, schema)
    required = {"schema_version", "report_metadata", "project", "data_profile", "indicator_facts", "problem_diagnoses", "overall_assessment", "cross_problem_analysis", "measure_recommendations", "report_assets", "audit"}
    errors.extend(f"missing_top_level:{name}" for name in sorted(required - set(report)))
    if report.get("schema_version") not in {"2.3-mvp", "2.4-mvp", "2.5-mvp"}:
        errors.append("invalid_schema_version")
    if report.get("schema_version") in {"2.4-mvp", "2.5-mvp"}:
        spatial = report.get("spatial_evidence")
        if not isinstance(spatial, dict):
            errors.append("missing_spatial_evidence")
        elif spatial.get("landscape_class_semantics") not in {"unknown_codes", "mapped_cls_v1"}:
            errors.append("invalid_landscape_class_semantics")
        risk = report.get("overall_assessment", {})
        if risk.get("risk_level_status") not in {"provided", "not_provided"}:
            errors.append("invalid_risk_level_status")
        if risk.get("risk_level_status") == "provided" and not risk.get("risk_level"):
            errors.append("missing_provided_risk_level")
    if report.get("schema_version") == "2.5-mvp":
        evidence = report.get("risk_evidence")
        if not isinstance(evidence, dict):
            errors.append("missing_risk_evidence")
        else:
            for name in ("current", "trend", "spatial_gradient", "annual_series", "source_scope", "quality_flags"):
                if name not in evidence:
                    errors.append(f"missing_risk_evidence:{name}")
            if not isinstance(evidence.get("current"), dict):
                errors.append("invalid_risk_evidence:current")
            elif any(field not in evidence["current"] for field in ("risk_level", "risk_level_code", "values")):
                errors.append("invalid_risk_evidence:current_fields")
            if not isinstance(evidence.get("trend"), dict) or evidence.get("trend", {}).get("aggregation") != "multi_scope_mean":
                errors.append("invalid_risk_evidence:trend")
            if not isinstance(evidence.get("spatial_gradient"), list):
                errors.append("invalid_risk_evidence:spatial_gradient")
            if not isinstance(evidence.get("annual_series"), list):
                errors.append("invalid_risk_evidence:annual_series")
            if not isinstance(evidence.get("quality_flags"), list):
                errors.append("invalid_risk_evidence:quality_flags")
    diagnoses = {item["problem_id"]: item for item in report.get("problem_diagnoses", [])}
    for problem_id, item in diagnoses.items():
        if item["status"] == "数据不足" and item["existence"] == "confirmed":
            errors.append(f"insufficient_confirmed:{problem_id}")
        if not item.get("analysis_features") or "analysis_context" not in item:
            errors.append(f"missing_analysis_context:{problem_id}")
        if "diagnosis" not in item:
            errors.append(f"missing_nested_diagnosis:{problem_id}")
        if "provenance" not in item:
            errors.append(f"missing_problem_provenance:{problem_id}")
        for evidence in item.get("evidence", []):
            if not evidence.get("evidence_id") or not evidence.get("fact_ref"):
                errors.append(f"missing_evidence_provenance:{problem_id}")
    for measure in report.get("measure_recommendations", []):
        if any(diagnoses.get(problem_id, {}).get("status") in {"数据不足", "不适用"} for problem_id in measure["target_problem_ids"]):
            errors.append(f"measure_for_omitted_problem:{measure['measure_id']}")
        if "priority" in measure:
            errors.append(f"measure_priority_present:{measure['measure_id']}")
        if measure.get("parameter_status") == "not_provided" and measure.get("implementation_parameters") is not None:
            errors.append(f"invented_parameters:{measure['measure_id']}")
    return errors


def build_report_ir_mvp(
    workbook_path: str | Path,
    project_type: str,
    fid: int,
    target_year: int | None = None,
    project_name: str | None = None,
    data_scope: DataScope | None = None,
    generated_date: str | None = None,
    prepared_by: str = "生态状况评估编制组",
    analysis_backend: Any = None,
) -> dict[str, Any]:
    project = ProjectType(project_type)
    series = load_project_series(workbook_path, project, fid)
    if target_year is not None:
        selected = [item for item in series if item.year == int(target_year)]
        if not selected:
            raise InputValidationError(f"No record found for project={project.value}, FID={fid}, YEAR={target_year}")
        target = selected[0]
        series = [item for item in series if item.year <= target.year]
    else:
        target = series[-1]
    scope = data_scope or DataScope()
    facts = build_indicator_facts(series)
    risk_types = split_risk_types(target.text("RISK_TYPES"))
    rules = load_json_config("problem_rules.json")
    diagnoses = build_problem_diagnoses(project.value, risk_types, facts, scope, rules)
    measures, measure_audit = build_measure_recommendations(target.text("MEASURE_TEXT"), project.value, scope, diagnoses)
    analysis_config = load_json_config("analysis_config.json")
    project_record = {"fid": int(fid), "name": project_name or f"FID {fid}项目", "project_type": project.value, "project_attributes": asdict(scope)}
    diagnoses, cross_problem_analysis, analysis_backend_name = build_analysis_material(
        project_record, diagnoses, facts, measures, rules, analysis_config, analysis_backend
    )
    available = [item["indicator"] for item in facts if item["valid"]]
    missing = [item["indicator"] for item in facts if not item["valid"]]
    report = {
        "schema_version": "2.3-mvp",
        "report_metadata": {"report_id": f"eco-{project.value}-{fid}-{target.year}", "report_title": "生态状况智能诊断与修复决策报告", "current_year": target.year, "evaluation_start_year": series[0].year, "evaluation_end_year": target.year, "generated_date": generated_date or date.today().isoformat(), "prepared_by": prepared_by},
        "project": project_record,
        "data_profile": {"available_indicators": available, "missing_indicators": missing, "known_limitations": sorted({limitation for item in diagnoses for limitation in item["limitations"]})},
        "indicator_facts": facts,
        "problem_diagnoses": diagnoses,
        "overall_assessment": {"risk_level": target.text("RISK_LEVEL"), "risk_level_code": target.number("RISK_LEVEL_CODE"), "reported_risk_types": risk_types},
        "cross_problem_analysis": cross_problem_analysis,
        "measure_recommendations": measures,
        "report_assets": {"charts": [], "tables": []},
        "audit": {
            "source_sheet": project.value,
            "source_row": target.source_row,
            "source_years": [item.year for item in series],
            "schema_config": "config/reportir_schema.json",
            "rule_config": "config/problem_rules.json",
            "trend_config": "config/trend_config.json",
            "analysis_config": "config/analysis_config.json",
            "analysis_backend": analysis_backend_name,
            "analysis_fallback_problem_ids": list(getattr(analysis_backend, "fallback_problem_ids", [])),
            "analysis_cross_fallback": bool(getattr(analysis_backend, "cross_fallback", False)),
            "analysis_request_count": int(getattr(analysis_backend, "request_count", 0)),
            "analysis_request_durations_seconds": list(
                getattr(analysis_backend, "request_durations_seconds", [])
            ),
            "analysis_request_seconds": round(
                sum(getattr(analysis_backend, "request_durations_seconds", [])), 3
            ),
            "priority_module_enabled": False,
            "measure_config": "config/measure_knowledge.json",
            "measure_filter": measure_audit,
        },
    }
    errors = validate_report_ir_mvp(report)
    if errors:
        raise InputValidationError("Invalid ReportIR 2.3-mvp: " + ", ".join(errors))
    return report
