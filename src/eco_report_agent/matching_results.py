from __future__ import annotations

from pathlib import Path
from typing import Any

from .checkpointing import atomic_write_json, file_sha256, read_json
from .xlsx_reader import XlsxWorkbook


MATCHING_SHEETS = ("solar", "wind", "coal")


def _integer(value: Any, field: str, row_number: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {row_number}: invalid {field}") from exc


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_matching_results(
    workbook_path: str | Path, target_year: int
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Read one target-year, project-keyed matching result per project."""
    source = Path(workbook_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    source_hash = file_sha256(source)
    results: dict[str, dict[str, Any]] = {}
    with XlsxWorkbook(source) as workbook:
        missing = set(MATCHING_SHEETS) - set(workbook.sheet_names)
        if missing:
            raise ValueError(f"Missing matching sheets: {sorted(missing)}")
        for sheet in MATCHING_SHEETS:
            for row_number, record in workbook.iter_records(sheet):
                project_type = _text(record.get("PROJECT_TYPE")).lower()
                if project_type != sheet:
                    raise ValueError(f"Row {row_number}: PROJECT_TYPE must be {sheet}")
                year = _integer(record.get("YEAR"), "YEAR", row_number)
                if year != int(target_year):
                    continue
                fid = _integer(record.get("FID"), "FID", row_number)
                key = f"{project_type}-{fid:04d}"
                if key in results:
                    raise ValueError(f"Duplicate matching record for {key}/{target_year}")
                code_value = record.get("RISK_LEVEL_CODE")
                try:
                    risk_level_code = int(float(str(code_value))) if code_value not in {None, ""} else None
                except (TypeError, ValueError):
                    risk_level_code = None
                results[key] = {
                    "source_workbook_sha256": source_hash,
                    "source_sheet": sheet,
                    "source_row": row_number,
                    "target_year": int(target_year),
                    "risk_level": _text(record.get("RISK_LEVEL")) or None,
                    "risk_level_code": risk_level_code,
                    "risk_types": _text(record.get("RISK_TYPES")),
                    "measure_text": _text(record.get("MEASURE_TEXT")),
                }
    return source_hash, results

def attach_matching_results(snapshot_root: str | Path, manifest: dict[str, Any], workbook_path: str | Path, target_year: int) -> dict[str, Any]:
    source_hash, matches = load_matching_results(workbook_path, target_year)
    root = Path(snapshot_root)
    expected = {item["project_key"] for item in manifest["projects"]}
    missing = sorted(expected - set(matches))
    extra = sorted(set(matches) - expected)
    if missing or extra:
        raise ValueError(f"Matching project keys differ: missing={missing[:5]}, extra={extra[:5]}")
    if manifest.get("matching_results_sha256") == source_hash:
        return manifest
    from .raw_batch import _set_snapshot_hashes
    for entry in manifest["projects"]:
        path = root / entry["snapshot_path"]
        snapshot = read_json(path)
        snapshot["matching_result"] = matches[entry["project_key"]]
        _set_snapshot_hashes(snapshot)
        atomic_write_json(path, snapshot)
        entry["snapshot_sha256"] = snapshot["snapshot_sha256"]
        entry["analysis_snapshot_sha256"] = snapshot["analysis_snapshot_sha256"]
    manifest["matching_results_sha256"] = source_hash
    atomic_write_json(root / "input_manifest.json", manifest)
    return manifest
