from __future__ import annotations

from pathlib import Path

from .models import Observation, ProjectType
from .xlsx_reader import XlsxWorkbook


REQUIRED_COLUMNS = {
    "PROJECT_TYPE",
    "FID",
    "YEAR",
    "MEAN_NDVI",
    "MEAN_NPP",
    "CA",
    "LPI",
    "NP",
    "PD",
    "ED",
    "LSI",
    "ERI",
    "RISK_LEVEL",
    "RISK_LEVEL_CODE",
    "RISK_TYPES",
    "MEASURE_TEXT",
}


class InputValidationError(ValueError):
    pass


def _as_int(value: object, field_name: str, row_number: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise InputValidationError(f"Row {row_number}: invalid {field_name}={value!r}") from exc


def load_project_series(
    workbook_path: str | Path,
    project_type: ProjectType | str,
    fid: int,
) -> list[Observation]:
    project = ProjectType(project_type)
    observations: list[Observation] = []
    with XlsxWorkbook(workbook_path) as workbook:
        if project.value not in workbook.sheet_names:
            raise InputValidationError(
                f"Missing sheet {project.value!r}; available sheets: {workbook.sheet_names}"
            )
        for row_number, record in workbook.iter_records(project.value):
            if row_number == 2:
                missing = sorted(REQUIRED_COLUMNS.difference(record))
                if missing:
                    raise InputValidationError(f"Missing required columns: {missing}")
            record_fid = _as_int(record.get("FID"), "FID", row_number)
            if record_fid != int(fid):
                continue
            record_project = str(record.get("PROJECT_TYPE", "")).strip().lower()
            if record_project != project.value:
                raise InputValidationError(
                    f"Row {row_number}: PROJECT_TYPE={record_project!r} conflicts with sheet {project.value!r}"
                )
            year = _as_int(record.get("YEAR"), "YEAR", row_number)
            observations.append(
                Observation(
                    project_type=project,
                    fid=record_fid,
                    year=year,
                    values=record,
                    source_row=row_number,
                )
            )
    if not observations:
        raise InputValidationError(f"No records found for project={project.value}, FID={fid}")
    observations.sort(key=lambda item: item.year)
    years = [item.year for item in observations]
    if len(years) != len(set(years)):
        raise InputValidationError(f"Duplicate years found for project={project.value}, FID={fid}")
    return observations
