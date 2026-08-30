from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


SMOKE_FID = 900001
SMOKE_PROJECT = "coal"
SMOKE_YEAR = 2025

HEADERS = (
    "PROJECT_TYPE", "FID", "YEAR", "MEAN_NDVI", "STD_NDVI", "MEAN_NPP",
    "STD_NPP", "CA", "LPI", "NP", "PD", "ED", "LSI", "MEAN_VCI",
    "MEAN_EVI", "MEAN_SIF", "MEAN_VCS_C", "MEAN_VCS_CO2e", "ERI", "LER",
    "ES", "EP", "RER", "CVR", "RISK_LEVEL", "RISK_LEVEL_CODE",
    "RISK_TYPES", "MEASURE_TEXT", "ML_PREDICTION", "ML_ENABLED",
)


def _column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell(reference: str, value: object) -> str:
    if value is None:
        return f'<c r="{reference}"/>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _row(year: int, ndvi: float, measure: str) -> dict[str, object]:
    return {
        "PROJECT_TYPE": SMOKE_PROJECT,
        "FID": SMOKE_FID,
        "YEAR": year,
        "MEAN_NDVI": ndvi,
        "STD_NDVI": 0.1,
        "MEAN_NPP": 0.2,
        "STD_NPP": 0.1,
        "CA": 100,
        "LPI": 60,
        "NP": 20,
        "PD": 2,
        "ED": 1.2,
        "LSI": 1.4,
        "MEAN_VCI": 50,
        "MEAN_EVI": 0.1,
        "MEAN_SIF": 0.01,
        "MEAN_VCS_C": None,
        "MEAN_VCS_CO2e": None,
        "ERI": 0.1,
        "LER": 0.2,
        "ES": 0.3,
        "EP": 0.4,
        "RER": 0.5,
        "CVR": 0.6,
        "RISK_LEVEL": "III_中风险",
        "RISK_LEVEL_CODE": 3,
        "RISK_TYPES": "植被退化",
        "MEASURE_TEXT": measure,
        "ML_PREDICTION": "",
        "ML_ENABLED": "否",
    }


def _zip_entry(name: str, payload: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o444 << 16
    return info, payload.encode("utf-8")


def build_smoke_workbook() -> bytes:
    rows = [
        dict(zip(HEADERS, HEADERS)),
        _row(2024, 0.3, "乡土植被恢复"),
        _row(SMOKE_YEAR, 0.4, "乡土植被恢复；生态监测"),
    ]
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
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
        '<sheets><sheet name="coal" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in (
            ("xl/workbook.xml", workbook_xml),
            ("xl/_rels/workbook.xml.rels", relationships_xml),
            ("xl/worksheets/sheet1.xml", sheet_xml),
        ):
            info, data = _zip_entry(name, payload)
            archive.writestr(info, data)
    return buffer.getvalue()
