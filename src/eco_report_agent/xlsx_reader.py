from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree as ET


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF_RE = re.compile(r"([A-Z]+)(\d+)")


class XlsxFormatError(ValueError):
    pass


def _column_index(cell_ref: str) -> int:
    match = _CELL_REF_RE.fullmatch(cell_ref)
    if not match:
        raise XlsxFormatError(f"Invalid cell reference: {cell_ref}")
    result = 0
    for char in match.group(1):
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def _parse_number(value: str) -> int | float:
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


class XlsxWorkbook:
    """Small read-only XLSX reader using only the Python standard library."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._archive = zipfile.ZipFile(self.path)
        self._shared_strings = self._load_shared_strings()
        self._sheets = self._load_sheet_paths()

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> "XlsxWorkbook":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(self._sheets)

    def _load_shared_strings(self) -> list[str]:
        try:
            xml = self._archive.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        root = ET.fromstring(xml)
        strings: list[str] = []
        for item in root.findall(f"{{{_MAIN_NS}}}si"):
            text = "".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t"))
            strings.append(text)
        return strings

    def _load_sheet_paths(self) -> dict[str, str]:
        workbook = ET.fromstring(self._archive.read("xl/workbook.xml"))
        relations = ET.fromstring(self._archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in relations.findall(f"{{{_PKG_REL_NS}}}Relationship")
        }
        result: dict[str, str] = {}
        sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
        if sheets is None:
            raise XlsxFormatError("Workbook contains no sheets")
        for sheet in sheets:
            name = sheet.attrib["name"]
            relation_id = sheet.attrib[f"{{{_DOC_REL_NS}}}id"]
            target = targets[relation_id].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            result[name] = target
        return result

    def _cell_value(self, cell: ET.Element) -> Any:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.iter(f"{{{_MAIN_NS}}}t"))
        value_node = cell.find(f"{{{_MAIN_NS}}}v")
        if value_node is None or value_node.text is None:
            return None
        raw = value_node.text
        if cell_type == "s":
            return self._shared_strings[int(raw)]
        if cell_type == "b":
            return raw == "1"
        if cell_type in {"str", "e"}:
            return raw
        return _parse_number(raw)

    def iter_rows(self, sheet_name: str) -> Iterator[tuple[int, list[Any]]]:
        try:
            path = self._sheets[sheet_name]
        except KeyError as exc:
            raise KeyError(f"Unknown sheet {sheet_name!r}; available: {self.sheet_names}") from exc
        with self._archive.open(path) as handle:
            for event, element in ET.iterparse(handle, events=("end",)):
                if element.tag != f"{{{_MAIN_NS}}}row":
                    continue
                row_number = int(element.attrib.get("r", "0"))
                cells: dict[int, Any] = {}
                for cell in element.findall(f"{{{_MAIN_NS}}}c"):
                    ref = cell.attrib.get("r")
                    if ref:
                        cells[_column_index(ref)] = self._cell_value(cell)
                max_index = max(cells, default=-1)
                yield row_number, [cells.get(index) for index in range(max_index + 1)]
                element.clear()

    def iter_records(self, sheet_name: str) -> Iterator[tuple[int, dict[str, Any]]]:
        rows = self.iter_rows(sheet_name)
        try:
            _, header_row = next(rows)
        except StopIteration as exc:
            raise XlsxFormatError(f"Sheet {sheet_name!r} is empty") from exc
        headers = [str(value).strip() if value is not None else "" for value in header_row]
        if not any(headers):
            raise XlsxFormatError(f"Sheet {sheet_name!r} has an empty header")
        for row_number, values in rows:
            record = {
                header: values[index] if index < len(values) else None
                for index, header in enumerate(headers)
                if header
            }
            yield row_number, record
