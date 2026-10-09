#!/usr/bin/env python3
"""Export successful reports and reproducible chart data as portable NDJSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]+)\)")
NAMESPACE = uuid.UUID("41fdcf44-45e4-4da9-a9a4-c571331cb45f")


def _facts(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(x["indicator"]): x for x in ir.get("indicator_facts", []) if x.get("valid", True) and x.get("indicator")}


def _points(fact: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"year": int(x["year"]), "value": float(x["value"])} for x in sorted(fact.get("time_series", []), key=lambda x: x["year"])]


def vegetation(ir: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(ir)
    series = [{"name": label, "indicator": code, "points": _points(facts[code])} for code, label in (("MEAN_NDVI", "NDVI"), ("MEAN_NPP", "NPP")) if code in facts]
    return {"schema_version": 1, "title": "关键植被指标时序图", "x_field": "year", "y_field": "value", "x_label": "年份", "series": series}


def landscape(ir: dict[str, Any]) -> dict[str, Any]:
    facts, series = _facts(ir), []
    for code in ("NP", "PD", "LPI"):
        if code not in facts:
            continue
        raw = _points(facts[code])
        if not raw or raw[0]["value"] == 0:
            continue
        baseline = raw[0]["value"]
        series.append({"name": code, "indicator": code, "baseline_year": raw[0]["year"], "baseline_value": baseline, "points": [{"year": x["year"], "value": 100.0 * x["value"] / baseline} for x in raw]})
    return {"schema_version": 1, "title": "关键景观指标时序图", "x_field": "year", "y_field": "value", "x_label": "年份", "y_label": "相对指数（起始年 = 100）", "reference_lines": [{"axis": "y", "value": 100}], "series": series}


def matrix(ir: dict[str, Any]) -> dict[str, Any]:
    severity = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}
    trend = {"worsening": "恶化", "recently_worsening": "近期恶化", "mixed": "波动", "stable": "稳定", "recently_improving": "近期改善", "improving": "改善", "unknown": "未知"}
    rows = [{"problem_id": x.get("problem_id"), "problem_name": x.get("problem_name"), "status": x.get("status"), "severity": severity.get(x.get("severity", "unknown"), "未知"), "trend": trend.get(x.get("trend", "unknown"), "未知")} for x in ir.get("problem_diagnoses", []) if x.get("status") not in {"数据不足", "不适用"}]
    rows.sort(key=lambda x: (0 if x["status"] == "突出问题" else 1, x["problem_id"] or ""))
    return {"schema_version": 1, "title": "生态问题诊断矩阵", "columns": ["problem_name", "status", "severity", "trend"], "rows": rows}


def risk(ir: dict[str, Any]) -> dict[str, Any]:
    evidence = ir.get("risk_evidence") or ir.get("spatial_evidence", {}).get("risk_evidence") or {}
    return {
        "schema_version": 1,
        "title": "综合生态风险趋势与空间梯度",
        "annual_aggregation": "multi_scope_mean",
        "annual_series": [
            {"year": int(item["year"]), "value": float(item["mean_ceri"]), "valid_scope_count": int(item.get("valid_scope_count", 0))}
            for item in evidence.get("annual_series", [])
            if item.get("mean_ceri") is not None
        ],
        "target_year": int(evidence.get("target_year", ir.get("report_metadata", {}).get("current_year", 0))),
        "spatial_gradient": [
            {"scope": item.get("scope"), "value": float((item.get("values") or {}).get("CERI")), "risk_level": item.get("risk_level")}
            for item in evidence.get("spatial_gradient", [])
            if (item.get("values") or {}).get("CERI") is not None
        ],
        "source_scope": evidence.get("source_scope"),
    }


BUILDERS: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "figure-2-1": ("line", vegetation),
    "figure-2-2": ("line", landscape),
    "figure-2-3": ("risk_combo", risk),
    "figure-5-1": ("matrix", matrix),
}


def transform_report(markdown: str, ir: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    matches = list(IMAGE_RE.finditer(markdown))
    assets = ir.get("report_assets", {}).get("charts", [])
    if not assets:
        legacy_ids = ["figure-2-1", "figure-2-2", "figure-5-1"]
        if len(matches) == 4 and ir.get("risk_evidence") is not None:
            legacy_ids.insert(2, "figure-2-3")
        assets = [{"asset_id": asset_id} for asset_id in legacy_ids]
    if len(matches) != len(assets):
        raise ValueError(f"expected {len(assets)} Markdown images, found {len(matches)}")
    parts, charts, cursor = [], [], 0
    for index, match in enumerate(matches, 1):
        parts.extend((markdown[cursor:match.start()], f"{{{{chart:{index}}}}}"))
        cursor = match.end()
        asset = assets[index - 1] if index - 1 < len(assets) else {}
        asset_id = str(asset.get("asset_id", ""))
        if asset_id not in BUILDERS:
            raise ValueError(f"no chart builder for asset {asset_id!r}")
        chart_type, builder = BUILDERS[asset_id]
        charts.append({"insert_key": index, "type": chart_type, "data_json": builder(ir)})
    parts.append(markdown[cursor:])
    content = "".join(parts)
    if IMAGE_RE.search(content) or re.search(r"https?://", content):
        raise ValueError("image link or URL remains in Markdown")
    return content, charts


def _valid(result: Path) -> bool:
    path = result / "validation_report.json"
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return bool(data.get("valid")) and data.get("writer_backend") == "llm" and not data.get("writer_fallback_reason")


def export_batch(batch: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    paths = {name: output / name for name in ("report_entry.ndjson", "report_chart.ndjson", "project_report_id.ndjson")}
    report_count = chart_count = 0
    failures: list[dict[str, str]] = []
    with paths["report_entry.ndjson"].open("w", encoding="utf-8", newline="\n") as reports, paths["report_chart.ndjson"].open("w", encoding="utf-8", newline="\n") as charts_out, paths["project_report_id.ndjson"].open("w", encoding="utf-8", newline="\n") as mapping:
        for md_path in sorted(batch.glob("projects/*/fid-*/result/report.md")):
            result = md_path.parent
            if not _valid(result):
                continue
            try:
                ir = json.loads((result / "report_ir.json").read_text(encoding="utf-8"))
                content, charts = transform_report(md_path.read_text(encoding="utf-8"), ir)
                project_key = str(ir.get("report_metadata", {}).get("project_key") or result.parent.name)
                report_id = uuid.uuid5(NAMESPACE, project_key).hex
                reports.write(json.dumps({"id": report_id, "content": content, "type": "报告"}, ensure_ascii=False) + "\n")
                mapping.write(json.dumps({"project_key": project_key, "report_id": report_id}, ensure_ascii=False) + "\n")
                for chart in charts:
                    charts_out.write(json.dumps({"report_id": report_id, **chart}, ensure_ascii=False) + "\n")
                    chart_count += 1
                report_count += 1
            except Exception as exc:
                failures.append({"path": str(md_path.relative_to(batch)), "error": f"{type(exc).__name__}: {exc}"})
    manifest: dict[str, Any] = {"format_version": 1, "report_count": report_count, "chart_count": chart_count, "failure_count": len(failures), "failures": failures, "files": {}}
    for path in paths.values():
        manifest["files"][path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export_batch(args.batch_dir, args.output_dir)
    print(json.dumps({k: result[k] for k in ("report_count", "chart_count", "failure_count")}, ensure_ascii=False))
    return 0 if not result["failure_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
