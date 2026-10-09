from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .document_blocks import DocumentBlock, parse_report_markdown
from .markdown_report import render_markdown_report
from .models import ReportIR
from .report_validation import validate_report_markdown


TABLE_COLUMN_WEIGHTS = {
    "问题ID": 0.75, "事实ID": 0.90, "措施ID": 0.70, "问题": 1.35, "措施": 1.20,
    "状态": 1.10, "严重程度": 0.85, "问题趋势": 1.05, "趋势": 1.05, "可信度": 0.85,
    "对应问题": 1.35, "作用机制": 1.35, "来源": 0.85,
    "限制说明": 1.80, "证据ID": 1.25, "指标": 1.10,
}


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_\allowbreak{}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "—": "-",
        "–": "-",
        "‑": "-",
    }
    return "".join(replacements.get(character, character) for character in text)


def _table_widths(headers: tuple[str, ...]) -> list[float]:
    weights = [TABLE_COLUMN_WEIGHTS.get(header, 1.0) for header in headers]
    total = sum(weights)
    return [0.86 * weight / total for weight in weights]


def _table_latex(block: DocumentBlock) -> list[str]:
    widths = _table_widths(block.rows[0])
    columns = [rf">{{\raggedright\arraybackslash}}p{{{width:.3f}\textwidth}}" for width in widths]
    specification = "".join(columns)
    header_values = "} & \\textbf{".join(_latex_escape(value) for value in block.rows[0])
    header = rf"\textbf{{{header_values}}} \\"
    lines = [
        r"{\fontsize{9}{11}\selectfont",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1.12}",
        r"\sloppy",
        rf"\begin{{longtable}}{{{specification}}}",
        r"\toprule",
        header,
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        header,
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endfoot",
    ]
    for row in block.rows[1:]:
        values = " & ".join(r"\hspace{0pt}" + _latex_escape(value) for value in row)
        lines.append(values + r" \\")
    lines.extend([r"\end{longtable}", r"}"])
    return lines


def render_latex_report(markdown: str, project_name: str, target_year: int) -> str:
    blocks = parse_report_markdown(markdown)
    body: list[str] = []
    in_list = False
    on_cover = True
    for block in blocks:
        if block.kind != "bullet" and in_list:
            body.append(r"\end{itemize}")
            in_list = False
        if block.kind == "pagebreak":
            body.append(r"\newpage")
            on_cover = False
        elif block.kind == "toc":
            body.extend([r"\setcounter{tocdepth}{2}", r"\tableofcontents"])
        elif block.kind == "caption":
            body.append(r"\begin{center}\fontsize{10.5}{13}\selectfont\bfseries " + _latex_escape(block.text) + r"\end{center}")
        elif block.kind == "bullet":
            if not in_list:
                body.append(r"\begin{itemize}")
                in_list = True
            body.append(r"\item " + _latex_escape(block.text))
        elif block.kind == "image":
            body.append(r"\begin{center}\includegraphics[width=0.92\textwidth]{\detokenize{" + block.source + r"}}\end{center}")
        elif block.kind == "heading":
            escaped = _latex_escape(block.text)
            if block.level == 1:
                body.extend(
                    [
                        r"\thispagestyle{empty}",
                        r"\begin{center}",
                        rf"{{\fontsize{{28}}{{34}}\selectfont\bfseries\color{{ReportInk}} {escaped}\par}}",
                        r"\vspace{0.75em}",
                        rf"{{\large\color{{ReportMuted}} {_latex_escape(project_name)} | {target_year}年\par}}",
                        r"\end{center}",
                        r"\vspace{1.2em}",
                    ]
                )
            else:
                command = {2: "section", 3: "subsection", 4: "subsubsection"}.get(block.level, "paragraph")
                body.append(r"\Needspace{6\baselineskip}")
                body.append(rf"\{command}*{{{escaped}}}")
                if block.text != "目录":
                    body.append(rf"\addcontentsline{{toc}}{{{command}}}{{{escaped}}}")
        elif block.kind == "table":
            body.extend(_table_latex(block))
        else:
            if on_cover:
                body.append(r"\begin{center}" + _latex_escape(block.text) + r"\end{center}")
            elif block.text.startswith("**关键词：**"):
                body.append(r"\textbf{关键词：}" + _latex_escape(block.text.removeprefix("**关键词：**")) + r"\par")
            else:
                body.append(_latex_escape(block.text) + r"\par")
    if in_list:
        body.append(r"\end{itemize}")
    header = _latex_escape(f"{project_name} | {target_year}年生态状况智能诊断与修复决策报告")
    return rf'''\documentclass[12pt]{{article}}
\usepackage[a4paper,top=2.54cm,bottom=2.54cm,left=3.0cm,right=2.6cm,headheight=15pt,headsep=16pt,footskip=28pt]{{geometry}}
\usepackage{{fontspec}}
\setmainfont[Path=/usr/share/fonts/google-droid/]{{DroidSansFallback.ttf}}
\XeTeXlinebreaklocale "zh"
\XeTeXlinebreakskip = 0pt plus 1pt
\usepackage[table]{{xcolor}}
\usepackage{{array,longtable,booktabs,enumitem,fancyhdr,hyperref,graphicx}}
\definecolor{{ReportBlue}}{{HTML}}{{2E74B5}}
\definecolor{{ReportDarkBlue}}{{HTML}}{{1F4D78}}
\definecolor{{ReportInk}}{{HTML}}{{0B2545}}
\definecolor{{ReportMuted}}{{HTML}}{{667788}}
\definecolor{{ReportTable}}{{HTML}}{{F4F6F9}}
\hypersetup{{hidelinks}}
\setlength{{\parindent}}{{2em}}
\setlength{{\parskip}}{{0pt}}
\setlength{{\LTpre}}{{4pt}}
\setlength{{\LTpost}}{{8pt}}
\renewcommand{{\arraystretch}}{{1.25}}
\setlist[itemize]{{leftmargin=2em,itemsep=4pt,topsep=2pt}}
\pagestyle{{fancy}}
\fancyhf{{}}
\fancyhead[R]{{\small\color{{ReportMuted}} {header}}}
\fancyfoot[C]{{\small 第\thepage 页}}
\renewcommand{{\headrulewidth}}{{0pt}}
\renewcommand{{\footrulewidth}}{{0pt}}
\makeatletter
\newcommand{{\Needspace}}[1]{{\par\begingroup\dimen@=#1\relax\dimen@ii=\pagegoal\advance\dimen@ii by -\pagetotal\ifdim\dimen@>\dimen@ii\newpage\fi\endgroup}}
\renewcommand\section{{\@startsection{{section}}{{1}}{{0pt}}{{18pt}}{{10pt}}{{\normalfont\fontsize{{16}}{{20}}\selectfont\bfseries\color{{ReportBlue}}}}}}
\renewcommand\subsection{{\@startsection{{subsection}}{{2}}{{0pt}}{{12pt}}{{6pt}}{{\normalfont\fontsize{{13}}{{16}}\selectfont\bfseries\color{{ReportBlue}}}}}}
\renewcommand\subsubsection{{\@startsection{{subsubsection}}{{3}}{{0pt}}{{8pt}}{{4pt}}{{\normalfont\fontsize{{12}}{{15}}\selectfont\bfseries\color{{ReportDarkBlue}}}}}}
\makeatother
\begin{{document}}
\sloppy
{chr(10).join(body)}
\end{{document}}
'''


def validate_pdf_bytes(payload: bytes) -> tuple[str, ...]:
    issues: list[str] = []
    if not payload.startswith(b"%PDF-"):
        issues.append("PDF header is missing")
    if b"%%EOF" not in payload[-2048:]:
        issues.append("PDF EOF marker is missing")
    if len(payload) < 4096:
        issues.append("PDF payload is unexpectedly small")
    return tuple(issues)


def write_pdf_report(
    report: ReportIR,
    output: str | Path,
    *,
    xelatex_command: str = "xelatex",
    timeout_seconds: int = 180,
) -> Path:
    executable = shutil.which(xelatex_command)
    if executable is None:
        raise RuntimeError("xelatex is required by the optional PDF renderer")
    markdown = render_markdown_report(report)
    validation = validate_report_markdown(markdown, report)
    if not validation.valid:
        raise ValueError("Markdown report failed validation before PDF rendering")
    project_name = report.project_name or f"FID {report.fid}项目"
    latex = render_latex_report(markdown, project_name, report.target_year)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eco-report-pdf-", dir=output_path.parent) as directory:
        temporary = Path(directory)
        source = temporary / "report.tex"
        source.write_text(latex, encoding="utf-8")
        command = [
            executable,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",
            f"-output-directory={temporary}",
            str(source),
        ]
        completed = None
        for _ in range(2):
            completed = subprocess.run(
                command,
                cwd=temporary,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                break
        assert completed is not None
        generated = temporary / "report.pdf"
        if completed.returncode != 0 or not generated.is_file():
            log_tail = completed.stdout[-4000:]
            raise RuntimeError(f"xelatex failed with exit code {completed.returncode}:\n{log_tail}")
        if "Missing character:" in completed.stdout:
            warnings = "\n".join(
                line for line in completed.stdout.splitlines() if "Missing character:" in line
            )
            raise RuntimeError(f"xelatex reported missing glyphs:\n{warnings[-4000:]}")
        payload = generated.read_bytes()
        issues = validate_pdf_bytes(payload)
        if issues:
            raise ValueError("PDF validation failed: " + "; ".join(issues))
        output_path.write_bytes(payload)
    return output_path


def write_pdf_markdown(
    markdown: str,
    project_name: str,
    target_year: int,
    output: str | Path,
    *,
    xelatex_command: str = "xelatex",
    timeout_seconds: int = 180,
) -> Path:
    """Render already validated Writer Markdown without rebuilding diagnostic facts."""
    executable = shutil.which(xelatex_command)
    if executable is None:
        raise RuntimeError("xelatex is required by the optional PDF renderer")
    latex = render_latex_report(markdown, project_name, target_year)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eco-report-pdf-", dir=output_path.parent) as directory:
        temporary = Path(directory)
        source = temporary / "report.tex"
        source.write_text(latex, encoding="utf-8")
        command = [
            executable,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",
            f"-output-directory={temporary}",
            str(source),
        ]
        completed = None
        for _ in range(2):
            completed = subprocess.run(
                command,
                cwd=temporary,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                break
        assert completed is not None
        generated = temporary / "report.pdf"
        if completed.returncode != 0 or not generated.is_file():
            raise RuntimeError(
                f"xelatex failed with exit code {completed.returncode}:\n{completed.stdout[-4000:]}"
            )
        if "Missing character:" in completed.stdout:
            warnings = "\n".join(
                line for line in completed.stdout.splitlines() if "Missing character:" in line
            )
            raise RuntimeError(f"xelatex reported missing glyphs:\n{warnings[-4000:]}")
        payload = generated.read_bytes()
        issues = validate_pdf_bytes(payload)
        if issues:
            raise ValueError("PDF validation failed: " + "; ".join(issues))
        output_path.write_bytes(payload)
    return output_path
