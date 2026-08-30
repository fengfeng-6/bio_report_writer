from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .docx_report import build_docx_report
from .markdown_report import render_markdown_report
from .pdf_report import write_pdf_report
from .pipeline import build_report_ir
from .report_validation import validate_report_markdown


@dataclass(frozen=True)
class FullReportBundle:
    directory: Path
    manifest: Path
    markdown: Path
    docx: Path
    pdf: Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_full_report_bundle(
    workbook_path: str | Path,
    project_type: str,
    fid: int,
    target_year: int | None,
    project_name: str | None,
    output_directory: str | Path,
) -> FullReportBundle:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=False)

    report = build_report_ir(workbook_path, project_type, fid, target_year, project_name)
    markdown_text = render_markdown_report(report)
    validation = validate_report_markdown(markdown_text, report)
    if not validation.valid:
        raise ValueError("Generated Markdown failed validation: " + ", ".join(validation.issues))

    markdown_path = output / "report.md"
    docx_path = output / "report.docx"
    pdf_path = output / "report.pdf"
    markdown_path.write_text(markdown_text, encoding="utf-8")
    docx_path.write_bytes(build_docx_report(report))
    write_pdf_report(report, pdf_path)

    files = {}
    for path in (markdown_path, docx_path, pdf_path):
        files[path.name] = {"sha256": _sha256(path), "size": path.stat().st_size}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "execution_location": "hpc-slurm",
                "project_type": report.project_type,
                "fid": report.fid,
                "target_year": report.target_year,
                "validation": validation.to_dict(),
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return FullReportBundle(
        directory=output,
        manifest=manifest_path,
        markdown=markdown_path,
        docx=docx_path,
        pdf=pdf_path,
    )
