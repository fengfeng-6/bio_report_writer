from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .checkpointing import atomic_write_json, file_sha256, read_json, value_sha256
from .xlsx_reader import XlsxWorkbook


FORMAT_VERSION = 1
TYPE_SHEETS = {"solar": "光伏", "wind": "风电", "coal": "煤矿"}
LANDSCAPE_SHEETS = {
    "solar": "光伏景观破碎指数",
    "wind": "风电景观破碎指数",
    "coal": "煤矿景观破碎指数",
}
TYPE_LABELS = {"光伏": "solar", "风电": "wind", "煤矿": "coal"}
MAIN_FIELDS = ("NDVI", "NPP", "NEP", "NCS", "EVI", "SIF", "VCI", "VCS", "VCR")
LANDSCAPE_FIELDS = ("CA", "LPI", "NP", "PD", "ED", "LSI")
RISK_SHEET = "生态风险指数"
RISK_TREND_SHEET = "生态风险指数变化趋势"
RISK_FIELDS = ("LER", "ES", "EP", "RER", "CVR", "CERI_raw", "CERI")


def _integer(value: Any, field: str, row_number: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {row_number}: invalid {field}={value!r}") from exc


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trend_project_type(project_label: str, row_number: int) -> str:
    """Resolve a trend-row project label without relying on the duplicated FID."""
    label = str(project_label or "").strip()
    matches = [label_name for label_name in TYPE_LABELS if label_name in label]
    if len(matches) != 1:
        raise ValueError(
            f"Row {row_number}: trend project label must contain exactly one known type: {label!r}"
        )
    return TYPE_LABELS[matches[0]]


def _project_key(project_type: str, fid: int) -> str:
    return f"{project_type}-{fid:04d}"


def _snapshot_path(root: Path, project_type: str, fid: int) -> Path:
    return root / "projects" / project_type / f"fid-{fid:04d}.json"


def _normalized_project_names(
    project_names: Mapping[str, str] | None,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_name in (project_names or {}).items():
        key = str(raw_key).strip()
        name = str(raw_name).strip()
        if not key or not name:
            raise ValueError("project name map keys and values must be non-empty")
        normalized[key] = name
    return normalized


def _set_snapshot_hashes(snapshot: dict[str, Any]) -> None:
    analysis_value = {
        key: value
        for key, value in snapshot.items()
        if key not in {"project_name", "snapshot_sha256", "analysis_snapshot_sha256"}
    }
    snapshot["analysis_snapshot_sha256"] = value_sha256(analysis_value)
    snapshot["snapshot_sha256"] = value_sha256(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )


def prepare_raw_batch(
    workbook_path: str | Path,
    snapshot_root: str | Path,
    target_year: int,
    *,
    resume: bool = False,
    project_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = Path(workbook_path)
    root = Path(snapshot_root)
    source_hash = file_sha256(source)
    name_map = _normalized_project_names(project_names)
    manifest_path = root / "input_manifest.json"
    if manifest_path.is_file():
        existing = read_json(manifest_path)
        if (
            resume
            and existing.get("source_workbook_sha256") == source_hash
            and existing.get("target_year") == int(target_year)
            and existing.get("format_version") == FORMAT_VERSION
        ):
            known_keys = {item["project_key"] for item in existing["projects"]}
            unknown_keys = sorted(set(name_map) - known_keys)
            if unknown_keys:
                raise ValueError(f"project name map contains unknown keys: {unknown_keys}")
            changed = False
            changed_keys: set[str] = set()
            for entry in existing["projects"]:
                key = entry["project_key"]
                if key not in name_map or entry.get("project_name") == name_map[key]:
                    continue
                path = root / entry["snapshot_path"]
                snapshot = read_json(path)
                snapshot["project_name"] = name_map[key]
                _set_snapshot_hashes(snapshot)
                atomic_write_json(path, snapshot)
                entry["project_name"] = snapshot["project_name"]
                entry["snapshot_sha256"] = snapshot["snapshot_sha256"]
                entry["analysis_snapshot_sha256"] = snapshot[
                    "analysis_snapshot_sha256"
                ]
                changed = True
                changed_keys.add(key)
            if changed:
                existing["project_name_mapping_sha256"] = value_sha256(name_map)
                atomic_write_json(manifest_path, existing)
                for entry in existing["projects"]:
                    if entry["project_key"] in changed_keys:
                        entry["_name_changed"] = True
            return existing
        raise FileExistsError(
            f"snapshot directory already prepared with different inputs: {root}"
        )

    projects: dict[tuple[str, int], dict[str, Any]] = {}
    sheet_counts: dict[str, int] = {}
    with XlsxWorkbook(source) as workbook:
        expected = set(TYPE_SHEETS.values()) | set(LANDSCAPE_SHEETS.values()) | {
            "保护区侵占情况"
        }
        missing_sheets = sorted(expected - set(workbook.sheet_names))
        if missing_sheets:
            raise ValueError(f"Missing raw workbook sheets: {missing_sheets}")

        for project_type, sheet_name in TYPE_SHEETS.items():
            count = 0
            for row_number, record in workbook.iter_records(sheet_name):
                count += 1
                fid = _integer(record.get("序号"), "序号", row_number)
                year = _integer(record.get("年份"), "年份", row_number)
                if year > target_year:
                    continue
                key = (project_type, fid)
                snapshot = projects.setdefault(
                    key,
                    {
                        "format_version": FORMAT_VERSION,
                        "project_key": _project_key(project_type, fid),
                        "project_type": project_type,
                        "fid": fid,
                        "target_year": int(target_year),
                        "project_name": name_map.get(
                            _project_key(project_type, fid),
                            f"{sheet_name}项目 FID {fid}",
                        ),
                        "main_records": [],
                        "landscape_records": [],
                        "protected_area_records": [],
                        "risk_records": [],
                        "risk_trend_record": None,
                        "risk_source_present": False,
                        "region": {"盟市": "", "旗县": ""},
                    },
                )
                snapshot["main_records"].append(
                    {
                        "source_sheet": sheet_name,
                        "source_row": row_number,
                        "year": year,
                        "scope": str(record.get("研究范围", "")).strip(),
                        "metrics": {name: _number(record.get(name)) for name in MAIN_FIELDS},
                    }
                )
            sheet_counts[sheet_name] = count

        for project_type, sheet_name in LANDSCAPE_SHEETS.items():
            count = 0
            for row_number, record in workbook.iter_records(sheet_name):
                count += 1
                fid = _integer(record.get("FID"), "FID", row_number)
                year = _integer(record.get("年份"), "年份", row_number)
                if year > target_year:
                    continue
                key = (project_type, fid)
                if key not in projects:
                    raise ValueError(
                        f"Landscape row has no primary project: {project_type}/{fid}"
                    )
                projects[key]["landscape_records"].append(
                    {
                        "source_sheet": sheet_name,
                        "source_row": row_number,
                        "year": year,
                        "scope": str(record.get("距离", "")).strip(),
                        "category": str(record.get("类别", "")).strip(),
                        "metrics": {
                            name: _number(record.get(name)) for name in LANDSCAPE_FIELDS
                        },
                    }
                )
            sheet_counts[sheet_name] = count

        count = 0
        for row_number, record in workbook.iter_records("保护区侵占情况"):
            count += 1
            project_type = TYPE_LABELS.get(str(record.get("类型", "")).strip())
            if project_type is None:
                raise ValueError(
                    f"Row {row_number}: unknown protected-area project type"
                )
            fid = _integer(record.get("FID"), "FID", row_number)
            key = (project_type, fid)
            if key not in projects:
                raise ValueError(
                    f"Protected-area row has no primary project: {project_type}/{fid}"
                )
            projects[key]["protected_area_records"].append(
                {
                    "source_sheet": "保护区侵占情况",
                    "source_row": row_number,
                    "scope": str(record.get("距离", "")).strip(),
                    "protected_area_type": str(record.get("保护区类型", "")).strip(),
                    "intersection_area_km2": _number(record.get("侵占面积(km²)")),
                }
            )
        sheet_counts["保护区侵占情况"] = count

        if RISK_SHEET in workbook.sheet_names:
            for snapshot in projects.values():
                snapshot["risk_source_present"] = True
            count = 0
            risk_keys: set[tuple[str, int, int, str]] = set()
            for row_number, record in workbook.iter_records(RISK_SHEET):
                count += 1
                project_type = TYPE_LABELS.get(str(record.get("类型", "")).strip())
                if project_type is None:
                    raise ValueError(f"Row {row_number}: unknown risk project type")
                fid = _integer(record.get("FID"), "FID", row_number)
                key = (project_type, fid)
                if key not in projects:
                    raise ValueError(f"Risk row has no primary project: {project_type}/{fid}")
                snapshot = projects[key]
                region = {"盟市": str(record.get("盟市", "")).strip(), "旗县": str(record.get("旗县", "")).strip()}
                year = _integer(record.get("年份"), "年份", row_number)
                scope = str(record.get("距离", "")).strip()
                unique_key = (project_type, fid, year, scope)
                if unique_key in risk_keys:
                    raise ValueError(f"Row {row_number}: duplicate risk key {unique_key!r}")
                risk_keys.add(unique_key)
                if any(region.values()):
                    existing_region = snapshot.get("region", {})
                    for name, value in region.items():
                        if value and existing_region.get(name) and existing_region[name] != value:
                            raise ValueError(
                                f"Row {row_number}: inconsistent region for {project_type}/{fid}: "
                                f"{name}={existing_region[name]!r} vs {value!r}"
                            )
                    snapshot["region"] = {
                        name: region.get(name) or existing_region.get(name, "")
                        for name in ("盟市", "旗县")
                    }
                snapshot["risk_records"].append(
                    {
                        "source_sheet": RISK_SHEET,
                        "source_row": row_number,
                        "year": year,
                        "scope": scope,
                        "metrics": {name: _number(record.get(name)) for name in RISK_FIELDS},
                        "risk_level": str(record.get("RISK_LEVEL", "")).strip(),
                        "risk_level_code": _number(record.get("RISK_LEVEL_CODE")),
                    }
                )
            sheet_counts[RISK_SHEET] = count

        if RISK_TREND_SHEET in workbook.sheet_names:
            for snapshot in projects.values():
                snapshot["risk_source_present"] = True
            count = 0
            for row_number, record in workbook.iter_records(RISK_TREND_SHEET):
                count += 1
                fid = _integer(record.get("FID"), "FID", row_number)
                project_label = str(record.get("项目", "")).strip()
                project_type = _trend_project_type(project_label, row_number)
                key = (project_type, fid)
                if key not in projects:
                    raise ValueError(
                        f"Row {row_number}: trend row has no primary project: {project_type}/{fid}"
                    )
                if projects[key].get("risk_trend_record") is not None:
                    raise ValueError(f"Row {row_number}: duplicate trend key {project_type}/{fid}")
                raw_p_value = _number(record.get("P_value"))
                p_value_invalid = raw_p_value is not None and not 0 <= raw_p_value <= 1
                p_value = raw_p_value
                if p_value_invalid:
                    p_value = None
                projects[key]["risk_trend_record"] = {
                    "source_sheet": RISK_TREND_SHEET,
                    "source_row": row_number,
                    "project_label": project_label,
                    "region": {"盟市": str(record.get("盟市", "")).strip(), "旗县": str(record.get("旗县", "")).strip()},
                    "start_ceri": _number(record.get("首年CERI")),
                    "end_ceri": _number(record.get("末年CERI")),
                    "change_rate_percent": _number(record.get("变化率(%)")),
                    "sen_slope": _number(record.get("Sen_slope")),
                    "p_value_raw": raw_p_value,
                    "p_value_invalid": p_value_invalid,
                    "p_value": p_value,
                    "trend_type": str(record.get("趋势类型", "")).strip(),
                }
                trend_region = projects[key]["risk_trend_record"]["region"]
                existing_region = projects[key].get("region", {})
                for name, value in trend_region.items():
                    if value and existing_region.get(name) and existing_region[name] != value:
                        raise ValueError(
                            f"Row {row_number}: trend region mismatch for {project_type}/{fid}: "
                            f"{name}={existing_region[name]!r} vs {value!r}"
                        )
                if any(trend_region.values()):
                    projects[key]["region"] = {
                        name: trend_region.get(name) or existing_region.get(name, "")
                        for name in ("盟市", "旗县")
                    }
            sheet_counts[RISK_TREND_SHEET] = count

    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for (project_type, fid), snapshot in sorted(projects.items()):
        years = sorted({item["year"] for item in snapshot["main_records"]})
        if target_year not in years:
            raise ValueError(f"Project {project_type}/{fid} has no target year {target_year}")
        snapshot["source_workbook_sha256"] = source_hash
        snapshot["source_years"] = years
        snapshot["protected_area_status"] = (
            "recorded" if snapshot["protected_area_records"] else "not_recorded"
        )
        _set_snapshot_hashes(snapshot)
        snapshot_hash = snapshot["snapshot_sha256"]
        path = _snapshot_path(root, project_type, fid)
        atomic_write_json(path, snapshot)
        counts[project_type] += 1
        entries.append(
            {
                "project_key": snapshot["project_key"],
                "project_type": project_type,
                "fid": fid,
                "project_name": snapshot["project_name"],
                "snapshot_path": path.relative_to(root).as_posix(),
                "snapshot_sha256": snapshot_hash,
                "analysis_snapshot_sha256": snapshot["analysis_snapshot_sha256"],
            }
        )

    unknown_keys = sorted(set(name_map) - {item["project_key"] for item in entries})
    if unknown_keys:
        raise ValueError(f"project name map contains unknown keys: {unknown_keys}")

    manifest = {
        "format_version": FORMAT_VERSION,
        "source_workbook": source.name,
        "source_workbook_sha256": source_hash,
        "target_year": int(target_year),
        "project_count": len(entries),
        "project_counts": dict(sorted(counts.items())),
        "sheet_row_counts": sheet_counts,
        "project_name_mapping_sha256": value_sha256(name_map),
        "projects": entries,
    }
    atomic_write_json(manifest_path, manifest)
    return manifest
