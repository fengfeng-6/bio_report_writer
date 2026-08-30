from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .docx_report import write_docx_report
from .pipeline import build_report_ir
from .markdown_report import render_markdown_report
from .pdf_report import write_pdf_report
from .report_validation import validate_report_markdown
from .source_catalog import (
    SourceRole,
    SourceSpec,
    build_source_catalog,
    write_source_catalog,
)


def _source_spec(value: str) -> SourceSpec:
    try:
        role_text, path_text = value.split("=", 1)
        role = SourceRole(role_text)
    except (ValueError, TypeError) as exc:
        choices = ", ".join(role.value for role in SourceRole)
        raise argparse.ArgumentTypeError(
            f"source must be ROLE=PATH; ROLE must be one of: {choices}"
        ) from exc
    if not path_text.strip():
        raise argparse.ArgumentTypeError("source path cannot be empty")
    return SourceSpec(role=role, path=Path(path_text))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eco-report")
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="Build deterministic ReportIR JSON")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    build.add_argument("--fid", required=True, type=int)
    build.add_argument("--year", type=int)
    build.add_argument("--output", type=Path)
    render = subcommands.add_parser("render-markdown", help="Render a deterministic Markdown report")
    render.add_argument("--input", required=True, type=Path)
    render.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    render.add_argument("--fid", required=True, type=int)
    render.add_argument("--year", type=int)
    render.add_argument("--project-name")
    render.add_argument("--output", required=True, type=Path)
    render_docx = subcommands.add_parser("render-docx", help="Render a deterministic Word report")
    render_docx.add_argument("--input", required=True, type=Path)
    render_docx.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    render_docx.add_argument("--fid", required=True, type=int)
    render_docx.add_argument("--year", type=int)
    render_docx.add_argument("--project-name")
    render_docx.add_argument("--output", required=True, type=Path)
    render_pdf = subcommands.add_parser("render-pdf", help="Render a deterministic PDF report with XeLaTeX")
    render_pdf.add_argument("--input", required=True, type=Path)
    render_pdf.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    render_pdf.add_argument("--fid", required=True, type=int)
    render_pdf.add_argument("--year", type=int)
    render_pdf.add_argument("--project-name")
    render_pdf.add_argument("--output", required=True, type=Path)
    render_all = subcommands.add_parser(
        "render-all", help="Render validated Markdown, Word and PDF outputs on HPC"
    )
    render_all.add_argument("--input", required=True, type=Path)
    render_all.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    render_all.add_argument("--fid", required=True, type=int)
    render_all.add_argument("--year", type=int)
    render_all.add_argument("--project-name")
    render_all.add_argument("--output-dir", required=True, type=Path)
    catalog = subcommands.add_parser(
        "catalog-sources",
        help="Build a role-tagged catalog and private knowledge pack from reference attachments",
    )
    catalog.add_argument(
        "--source",
        action="append",
        required=True,
        type=_source_spec,
        help="Repeatable ROLE=PATH source mapping",
    )
    catalog.add_argument("--manifest", required=True, type=Path)
    catalog.add_argument("--knowledge-pack", required=True, type=Path)
    validate = subcommands.add_parser("validate-markdown", help="Validate a generated Markdown report")
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    validate.add_argument("--fid", required=True, type=int)
    validate.add_argument("--year", type=int)
    validate.add_argument("--project-name")
    validate.add_argument("--report", required=True, type=Path)
    serve = subcommands.add_parser("serve", help="Serve the lightweight ReportIR HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        report = build_report_ir(args.input, args.project, args.fid, args.year)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return 0
    if args.command == "render-markdown":
        report = build_report_ir(args.input, args.project, args.fid, args.year, args.project_name)
        text = render_markdown_report(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        return 0
    if args.command == "render-docx":
        report = build_report_ir(args.input, args.project, args.fid, args.year, args.project_name)
        write_docx_report(report, args.output)
        return 0
    if args.command == "render-pdf":
        report = build_report_ir(args.input, args.project, args.fid, args.year, args.project_name)
        write_pdf_report(report, args.output)
        return 0
    if args.command == "render-all":
        from .full_report import build_full_report_bundle

        build_full_report_bundle(
            args.input,
            args.project,
            args.fid,
            args.year,
            args.project_name,
            args.output_dir,
        )
        return 0
    if args.command == "catalog-sources":
        catalog = build_source_catalog(args.source)
        write_source_catalog(catalog, args.manifest, args.knowledge_pack)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "entry_count": len(catalog.entries),
                    "block_count": len(catalog.blocks),
                    "fingerprint": catalog.fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-markdown":
        report = build_report_ir(args.input, args.project, args.fid, args.year, args.project_name)
        text = args.report.read_text(encoding="utf-8")
        result = validate_report_markdown(text, report)
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if result.valid else 1
    if args.command == "serve":
        from .service import serve

        workbook_path = os.environ.get("ECO_REPORT_WORKBOOK_PATH", "")
        service_token = os.environ.get("ECO_REPORT_SERVICE_TOKEN", "")
        enable_pdf_renderer = os.environ.get("ECO_REPORT_ENABLE_PDF_RENDERER", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        serve(workbook_path, service_token, args.host, args.port, enable_pdf_renderer)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
