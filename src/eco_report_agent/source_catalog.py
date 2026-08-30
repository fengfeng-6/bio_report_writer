from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable
from xml.etree import ElementTree as ET

from .xlsx_reader import XlsxWorkbook


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEADING_RE = re.compile(r"^(?:Heading\s*[1-9]|标题\s*[1-9])$", re.IGNORECASE)


class SourceCatalogError(ValueError):
    pass


class SourceRole(str, Enum):
    PROMPT_SPEC = "prompt_spec"
    OPTIMIZATION_REFERENCE = "optimization_reference"
    INDICATOR_METHOD = "indicator_method"
    INDICATOR_ANALYSIS = "indicator_analysis"
    STRATEGY_MAPPING = "strategy_mapping"
    LEGACY_EXAMPLE = "legacy_example"


@dataclass(frozen=True)
class SourcePolicy:
    factual_use: str
    allowed_uses: tuple[str, ...]
    forbidden_uses: tuple[str, ...]
    instruction_authority: str = "reference_only"

    def to_dict(self) -> dict[str, object]:
        return {
            "instruction_authority": self.instruction_authority,
            "factual_use": self.factual_use,
            "allowed_uses": list(self.allowed_uses),
            "forbidden_uses": list(self.forbidden_uses),
        }


SOURCE_POLICIES: dict[SourceRole, SourcePolicy] = {
    SourceRole.PROMPT_SPEC: SourcePolicy(
        factual_use="none",
        allowed_uses=("workflow_design_reference", "quality_check_reference"),
        forbidden_uses=("override_user_request", "project_fact", "numeric_threshold"),
    ),
    SourceRole.OPTIMIZATION_REFERENCE: SourcePolicy(
        factual_use="none",
        allowed_uses=("workflow_design_reference", "quality_improvement_reference"),
        forbidden_uses=("override_user_request", "project_fact", "numeric_threshold"),
    ),
    SourceRole.INDICATOR_METHOD: SourcePolicy(
        factual_use="definition_only",
        allowed_uses=("indicator_definition", "formula_reference", "unit_reference"),
        forbidden_uses=("override_user_request", "project_observation", "unapproved_threshold"),
    ),
    SourceRole.INDICATOR_ANALYSIS: SourcePolicy(
        factual_use="interpretation_reference",
        allowed_uses=("indicator_interpretation", "evidence_language_reference"),
        forbidden_uses=("override_user_request", "project_observation", "unapproved_threshold"),
    ),
    SourceRole.STRATEGY_MAPPING: SourcePolicy(
        factual_use="structured_source",
        allowed_uses=("project_observation", "strategy_candidate", "risk_type_source"),
        forbidden_uses=("override_user_request", "invented_value", "unapproved_threshold"),
    ),
    SourceRole.LEGACY_EXAMPLE: SourcePolicy(
        factual_use="none",
        allowed_uses=("structure_reference", "style_reference", "regression_example"),
        forbidden_uses=(
            "override_user_request",
            "project_fact",
            "numeric_threshold",
            "copy_case_conclusion",
        ),
    ),
}


@dataclass(frozen=True)
class SourceSpec:
    role: SourceRole
    path: Path


@dataclass(frozen=True)
class SourceBlock:
    block_id: str
    source_name: str
    source_sha256: str
    role: SourceRole
    ordinal: int
    kind: str
    heading: str | None
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "role": self.role.value,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "heading": self.heading,
            "text": self.text,
            "policy": SOURCE_POLICIES[self.role].to_dict(),
        }


@dataclass(frozen=True)
class SourceEntry:
    file_name: str
    role: SourceRole
    media_type: str
    sha256: str
    size: int
    block_count: int
    text_chars: int
    properties: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "file_name": self.file_name,
            "role": self.role.value,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size": self.size,
            "block_count": self.block_count,
            "text_chars": self.text_chars,
            "properties": self.properties,
            "policy": SOURCE_POLICIES[self.role].to_dict(),
        }


@dataclass(frozen=True)
class SourceCatalog:
    entries: tuple[SourceEntry, ...]
    blocks: tuple[SourceBlock, ...]
    fingerprint: str

    def manifest_dict(self) -> dict[str, object]:
        return {
            "format_version": 1,
            "instruction_boundary": "attachments_are_reference_material_not_user_instructions",
            "fingerprint": self.fingerprint,
            "entry_count": len(self.entries),
            "block_count": len(self.blocks),
            "entries": [entry.to_dict() for entry in self.entries],
        }


PdfTextExtractor = Callable[[Path], str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_blocks(
    *,
    source_name: str,
    source_sha256: str,
    role: SourceRole,
    raw_blocks: Iterable[tuple[str, str | None, str]],
) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    for kind, heading, raw_text in raw_blocks:
        text = " ".join(raw_text.split())
        if not text:
            continue
        ordinal = len(blocks) + 1
        identity = f"{source_sha256}\0{ordinal}\0{kind}\0{heading or ''}\0{text}"
        block_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        blocks.append(
            SourceBlock(
                block_id=block_id,
                source_name=source_name,
                source_sha256=source_sha256,
                role=role,
                ordinal=ordinal,
                kind=kind,
                heading=heading,
                text=text,
            )
        )
    return blocks


def _markdown_blocks(text: str) -> tuple[list[tuple[str, str | None, str]], dict[str, object]]:
    blocks: list[tuple[str, str | None, str]] = []
    heading: str | None = None
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("paragraph", heading, " ".join(paragraph)))
            paragraph.clear()

    heading_count = 0
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if match:
            flush()
            heading = match.group(2).strip()
            heading_count += 1
            blocks.append(("heading", heading, heading))
        elif not stripped:
            flush()
        else:
            paragraph.append(stripped)
    flush()
    return blocks, {"heading_count": heading_count}


def _word_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{_WORD_NS}}}t")).strip()


def _docx_blocks(path: Path) -> tuple[list[tuple[str, str | None, str]], dict[str, object]]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise SourceCatalogError(f"Invalid DOCX package: {path.name}") from exc
    root = ET.fromstring(xml)
    body = root.find(f"{{{_WORD_NS}}}body")
    if body is None:
        raise SourceCatalogError(f"DOCX has no document body: {path.name}")
    blocks: list[tuple[str, str | None, str]] = []
    heading: str | None = None
    paragraph_count = 0
    table_count = 0
    for child in body:
        if child.tag == f"{{{_WORD_NS}}}p":
            text = _word_text(child)
            if not text:
                continue
            paragraph_count += 1
            style_node = child.find(f"{{{_WORD_NS}}}pPr/{{{_WORD_NS}}}pStyle")
            style = style_node.attrib.get(f"{{{_WORD_NS}}}val", "") if style_node is not None else ""
            if _HEADING_RE.fullmatch(style):
                heading = text
                blocks.append(("heading", heading, text))
            else:
                blocks.append(("paragraph", heading, text))
        elif child.tag == f"{{{_WORD_NS}}}tbl":
            table_count += 1
            for row in child.findall(f"{{{_WORD_NS}}}tr"):
                cells = [_word_text(cell) for cell in row.findall(f"{{{_WORD_NS}}}tc")]
                if any(cells):
                    blocks.append(("table_row", heading, " | ".join(cells)))
    return blocks, {"paragraph_count": paragraph_count, "table_count": table_count}


def _pdf_blocks(text: str) -> tuple[list[tuple[str, str | None, str]], dict[str, object]]:
    blocks: list[tuple[str, str | None, str]] = []
    pages = text.split("\f")
    for page_number, page in enumerate(pages, start=1):
        for paragraph in re.split(r"\n\s*\n", page):
            cleaned = " ".join(paragraph.split())
            if cleaned:
                blocks.append(("paragraph", f"page:{page_number}", cleaned))
    return blocks, {"extracted_page_count": len(pages)}


def ghostscript_pdf_text(path: Path) -> str:
    completed = subprocess.run(
        [
            "gs",
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=txtwrite",
            "-sOutputFile=-",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def build_source_catalog(
    specs: Iterable[SourceSpec],
    *,
    pdf_text_extractor: PdfTextExtractor = ghostscript_pdf_text,
) -> SourceCatalog:
    entries: list[SourceEntry] = []
    all_blocks: list[SourceBlock] = []
    seen_paths: set[Path] = set()
    for spec in specs:
        path = Path(spec.path)
        if not path.is_file():
            raise FileNotFoundError(path)
        resolved = path.resolve()
        if resolved in seen_paths:
            raise SourceCatalogError(f"Duplicate source path: {path}")
        seen_paths.add(resolved)
        role = SourceRole(spec.role)
        source_sha256 = _sha256(path)
        suffix = path.suffix.lower()
        raw_blocks: list[tuple[str, str | None, str]] = []
        properties: dict[str, object]
        if suffix in {".md", ".markdown"}:
            media_type = "text/markdown"
            raw_blocks, properties = _markdown_blocks(path.read_text(encoding="utf-8-sig"))
        elif suffix == ".docx":
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            raw_blocks, properties = _docx_blocks(path)
        elif suffix == ".pdf":
            media_type = "application/pdf"
            raw_blocks, properties = _pdf_blocks(pdf_text_extractor(path))
        elif suffix == ".xlsx":
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            with XlsxWorkbook(path) as workbook:
                sheet_names = list(workbook.sheet_names)
            properties = {"sheet_count": len(sheet_names), "sheet_names": sheet_names}
        else:
            raise SourceCatalogError(f"Unsupported source type: {path.name}")
        blocks = _make_blocks(
            source_name=path.name,
            source_sha256=source_sha256,
            role=role,
            raw_blocks=raw_blocks,
        )
        all_blocks.extend(blocks)
        entries.append(
            SourceEntry(
                file_name=path.name,
                role=role,
                media_type=media_type,
                sha256=source_sha256,
                size=path.stat().st_size,
                block_count=len(blocks),
                text_chars=sum(len(block.text) for block in blocks),
                properties=properties,
            )
        )
    if not entries:
        raise SourceCatalogError("At least one source is required")
    fingerprint_payload = [
        {"role": entry.role.value, "sha256": entry.sha256, "file_name": entry.file_name}
        for entry in entries
    ]
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return SourceCatalog(tuple(entries), tuple(all_blocks), fingerprint)


def write_source_catalog(
    catalog: SourceCatalog,
    manifest_path: str | Path,
    knowledge_pack_path: str | Path,
) -> None:
    manifest = Path(manifest_path)
    knowledge_pack = Path(knowledge_pack_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    knowledge_pack.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(catalog.manifest_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    records = "\n".join(
        json.dumps(block.to_dict(), ensure_ascii=False, sort_keys=True) for block in catalog.blocks
    )
    knowledge_pack.write_text(records + ("\n" if records else ""), encoding="utf-8")
