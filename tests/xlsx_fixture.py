from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


HEADERS = [
    "PROJECT_TYPE", "FID", "YEAR", "MEAN_NDVI", "STD_NDVI", "MEAN_NPP", "STD_NPP",
    "CA", "LPI", "NP", "PD", "ED", "LSI", "MEAN_VCI", "MEAN_EVI", "MEAN_SIF",
    "MEAN_VCS_C", "MEAN_VCS_CO2e", "ERI", "LER", "ES", "EP", "RER", "CVR",
    "RISK_LEVEL", "RISK_LEVEL_CODE", "RISK_TYPES", "MEASURE_TEXT", "ML_PREDICTION", "ML_ENABLED",
]


def _column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell(ref: str, value: object) -> str:
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def make_workbook(path: Path, rows: list[dict[str, object]], sheet_name: str = "coal") -> Path:
    all_rows = [dict(zip(HEADERS, HEADERS)), *rows]
    row_xml = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = "".join(
            _cell(f"{_column_name(column_index)}{row_index}", row.get(header))
            for column_index, header in enumerate(HEADERS)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path
