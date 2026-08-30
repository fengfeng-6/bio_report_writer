from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


COLORS = ("2E5A87", "2E8B57", "D2691E", "B22222")
STATUS_SCORE = {"突出问题": 4, "存在风险": 3, "存在但改善中": 2, "未见明显恶化": 1, "数据不足": 0, "不适用": 0}


def _escape(value: str) -> str:
    return value.replace("_", r"\_").replace("%", r"\%")


def _compile_png(body: str, output: Path) -> None:
    output = output.resolve()
    xelatex = shutil.which("xelatex")
    gs = shutil.which("gs")
    if not xelatex or not gs:
        raise RuntimeError("xelatex and gs are required for chart rendering")
    output.parent.mkdir(parents=True, exist_ok=True)
    document = r"""\documentclass{article}
\usepackage[paperwidth=15cm,paperheight=10cm,margin=0.2cm]{geometry}
\pagestyle{empty}
\usepackage{fontspec}
\setmainfont[Path=/usr/share/fonts/google-droid/]{DroidSansFallback.ttf}
\usepackage{xcolor}
\usepackage{tikz}
\definecolor{c1}{HTML}{2E5A87}\definecolor{c2}{HTML}{2E8B57}
\definecolor{c3}{HTML}{D2691E}\definecolor{c4}{HTML}{B22222}
\begin{document}
""" + r"\noindent " + body + "\n" + r"\end{document}"
    with tempfile.TemporaryDirectory(prefix="eco-chart-", dir=output.parent) as directory:
        root = Path(directory)
        source = root / "chart.tex"
        source.write_text(document, encoding="utf-8")
        completed = subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", f"-output-directory={root}", str(source)], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        if completed.returncode != 0:
            raise RuntimeError("chart xelatex failed: " + completed.stdout[-3000:])
        completed = subprocess.run([gs, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pngalpha", "-r300", f"-sOutputFile={output}", str(root / "chart.pdf")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        if completed.returncode != 0 or not output.is_file():
            raise RuntimeError("chart ghostscript failed: " + completed.stdout[-3000:])


def _line_chart(facts: list[dict[str, Any]], indicators: list[str], title: str, output: Path) -> None:
    selected = [fact for fact in facts if fact["indicator"] in indicators and fact["time_series"]]
    commands = [r"\begin{tikzpicture}[x=0.9cm,y=5.5cm]", r"\draw[->] (0,0) -- (10,0);", r"\draw[->] (0,0) -- (0,1.12);", rf"\node[font=\bfseries] at (5,1.25) {{{_escape(title)}}};"]
    for line_index, fact in enumerate(selected):
        points = fact["time_series"]
        values = [float(item["value"]) for item in points]
        minimum, maximum = min(values), max(values)
        span = maximum - minimum or 1.0
        denominator = max(1, len(points) - 1)
        coordinates = []
        for index, item in enumerate(points):
            x = 9 * index / denominator
            y = 0.08 + 0.92 * (float(item["value"]) - minimum) / span
            coordinates.append(f"({x:.3f},{y:.3f})")
            commands.append(rf"\fill[c{line_index + 1}] ({x:.3f},{y:.3f}) circle (1.5pt);")
            commands.append(rf"\node[font=\scriptsize,rotate=45,anchor=east] at ({x:.3f},-0.03) {{{item['year']}}};")
        commands.append(rf"\draw[very thick,c{line_index + 1}] " + " -- ".join(coordinates) + ";")
        commands.append(rf"\node[anchor=west,text=c{line_index + 1},font=\small] at (9.2,{1.02 - line_index * 0.10:.3f}) {{{_escape(fact['indicator'])}}};")
    commands.append(r"\end{tikzpicture}")
    _compile_png("\n".join(commands), output)


def _matrix_chart(report: dict[str, Any], output: Path) -> None:
    priorities = {item["problem_id"]: item for item in report["priority_assessment"]}
    rows = report["problem_diagnoses"]
    commands = [r"\begin{tikzpicture}[x=1cm,y=0.55cm]", r"\node[font=\bfseries] at (5,1.5) {生态问题诊断矩阵};"]
    fills = {4: "red!70", 3: "orange!70", 2: "yellow!60", 1: "green!45", 0: "gray!30"}
    for index, item in enumerate(rows):
        y = -index
        score = STATUS_SCORE[item["status"]]
        priority = priorities[item["problem_id"]]["priority_level"]
        commands.append(rf"\node[anchor=east,font=\scriptsize] at (4.6,{y}) {{{_escape(item['problem_name'])}}};")
        commands.append(rf"\filldraw[fill={fills[score]},draw=white] (4.8,{y - 0.35}) rectangle (6.8,{y + 0.35});")
        commands.append(rf"\node[font=\scriptsize] at (5.8,{y}) {{{_escape(item['status'])}}};")
        commands.append(rf"\filldraw[fill=blue!15,draw=white] (7.0,{y - 0.35}) rectangle (8.2,{y + 0.35});")
        commands.append(rf"\node[font=\scriptsize] at (7.6,{y}) {{{priority}}};")
    commands.append(r"\end{tikzpicture}")
    _compile_png("\n".join(commands), output)


def build_report_charts(report: dict[str, Any], output_directory: str | Path) -> list[dict[str, Any]]:
    output = Path(output_directory)
    vegetation = output / "figure-2-1-vegetation.png"
    landscape = output / "figure-2-2-landscape.png"
    matrix = output / "figure-5-1-diagnosis-matrix.png"
    _line_chart(report["indicator_facts"], ["MEAN_NDVI", "MEAN_NPP"], "NDVI / NPP", vegetation)
    _line_chart(report["indicator_facts"], ["NP", "PD", "LPI"], "NP / PD / LPI", landscape)
    _matrix_chart(report, matrix)
    return [
        {"asset_id": "figure-2-1", "title": "关键植被指标时序图", "path": str(vegetation.resolve()), "dpi": 300},
        {"asset_id": "figure-2-2", "title": "关键景观指标时序图", "path": str(landscape.resolve()), "dpi": 300},
        {"asset_id": "figure-5-1", "title": "生态问题诊断矩阵", "path": str(matrix.resolve()), "dpi": 300},
    ]
