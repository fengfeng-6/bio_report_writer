from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentBlock:
    kind: str
    text: str = ""
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    source: str = ""


def _table_cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def _is_separator_row(cells: tuple[str, ...]) -> bool:
    return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)


def parse_report_markdown(markdown: str) -> list[DocumentBlock]:
    """Parse the controlled Markdown subset emitted by markdown_report.py."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[DocumentBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line == "<!-- pagebreak -->":
            blocks.append(DocumentBlock(kind="pagebreak"))
            index += 1
            continue
        if line == "<!-- toc -->":
            blocks.append(DocumentBlock(kind="toc"))
            index += 1
            continue
        if not line:
            index += 1
            continue
        if line.startswith("|") and line.endswith("|"):
            rows: list[tuple[str, ...]] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if not (candidate.startswith("|") and candidate.endswith("|")):
                    break
                cells = _table_cells(candidate)
                if not _is_separator_row(cells):
                    rows.append(cells)
                index += 1
            if rows:
                width = len(rows[0])
                normalized = tuple(row[:width] + ("",) * max(0, width - len(row)) for row in rows)
                blocks.append(DocumentBlock(kind="table", rows=normalized))
            continue
        if line.startswith("![") and "](" in line and line.endswith(")"):
            separator = line.index("](")
            blocks.append(DocumentBlock(kind="image", text=line[2:separator], source=line[separator + 2:-1]))
            index += 1
            continue
        if re.fullmatch(r"\*\*(?:表|图)\s+.+\*\*", line):
            blocks.append(DocumentBlock(kind="caption", text=line[2:-2].strip()))
            index += 1
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if level <= 4 and len(line) > level and line[level] == " ":
                blocks.append(DocumentBlock(kind="heading", level=level, text=line[level + 1 :].strip()))
                index += 1
                continue
        if line.startswith("- "):
            blocks.append(DocumentBlock(kind="bullet", text=line[2:].strip()))
        else:
            blocks.append(DocumentBlock(kind="paragraph", text=line))
        index += 1
    return blocks
