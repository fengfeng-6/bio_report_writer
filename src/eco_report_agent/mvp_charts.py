from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402


PALETTE = {
    "blue": "#315F8C",
    "light_blue": "#6F9FC6",
    "teal": "#3E8C86",
    "violet": "#8073AC",
    "red": "#B24A4A",
    "ink": "#263238",
    "muted": "#6B747C",
    "grid": "#D9DEE3",
    "prominent_fill": "#F4DEDE",
    "observation_fill": "#DFEBF3",
    "header_fill": "#EEF2F4",
}

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}
TREND_LABELS = {
    "worsening": "恶化",
    "recently_worsening": "近期恶化",
    "mixed": "波动",
    "stable": "稳定",
    "recently_improving": "近期改善",
    "improving": "改善",
    "unknown": "未知",
}
OMITTED_PROBLEM_STATUSES = {"数据不足", "不适用"}


def _configure_style() -> None:
    fallback = Path("/usr/share/fonts/google-droid/DroidSansFallback.ttf")
    if fallback.is_file():
        font_manager.fontManager.addfont(str(fallback))
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Noto Sans CJK SC", "Droid Sans Fallback", "DejaVu Sans"],
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "axes.edgecolor": PALETTE["ink"],
            "axes.labelcolor": PALETTE["ink"],
            "xtick.color": PALETTE["ink"],
            "ytick.color": PALETTE["ink"],
            "text.color": PALETTE["ink"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def _finish_axis(axis: Any) -> None:
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_linewidth(0.8)
    axis.spines["bottom"].set_linewidth(0.8)
    axis.tick_params(width=0.8, length=3)
    axis.grid(axis="y", color=PALETTE["grid"], linewidth=0.55, alpha=0.75)
    axis.set_axisbelow(True)


def _save_figure(figure: Any, png_path: Path) -> dict[str, str]:
    png_path = png_path.resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path = png_path.with_suffix(".svg")
    pdf_path = png_path.with_suffix(".pdf")
    figure.savefig(svg_path, bbox_inches="tight")
    figure.savefig(pdf_path, bbox_inches="tight")
    figure.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(figure)
    return {"path": str(png_path), "vector_path": str(svg_path), "pdf_path": str(pdf_path)}


def _fact_map(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["indicator"]: item for item in facts if item.get("valid", True)}


def _series(fact: dict[str, Any]) -> tuple[list[int], list[float]]:
    values = sorted(fact["time_series"], key=lambda item: item["year"])
    return [int(item["year"]) for item in values], [float(item["value"]) for item in values]


def _vegetation_chart(facts: list[dict[str, Any]], output: Path) -> dict[str, str]:
    fact_by_name = _fact_map(facts)
    figure, axes = plt.subplots(2, 1, figsize=(7.0, 4.8), sharex=True, constrained_layout=True)
    specifications = (
        (axes[0], "MEAN_NDVI", "NDVI", PALETTE["blue"], "a"),
        (axes[1], "MEAN_NPP", "NPP", PALETTE["teal"], "b"),
    )
    for axis, indicator, label, color, panel in specifications:
        years, values = _series(fact_by_name[indicator])
        axis.plot(years, values, color=color, linewidth=1.8, marker="o", markersize=4.2, markeredgewidth=0)
        axis.set_ylabel(label)
        axis.text(-0.09, 1.02, panel, transform=axis.transAxes, fontsize=10, fontweight="bold", va="bottom")
        axis.text(years[-1], values[-1], f"  {label}", color=color, va="center", fontsize=8.5)
        axis.margins(x=0.07, y=0.2)
        _finish_axis(axis)
    axes[-1].set_xlabel("年份")
    axes[-1].set_xticks(years)
    return _save_figure(figure, output)


def _indexed_landscape_chart(facts: list[dict[str, Any]], output: Path) -> dict[str, str]:
    fact_by_name = _fact_map(facts)
    figure, axis = plt.subplots(figsize=(7.0, 4.3), constrained_layout=True)
    styles = (
        ("NP", PALETTE["blue"], "o", 3.4, 6.2, 2),
        ("PD", PALETTE["teal"], "s", 1.6, 4.0, 3),
        ("LPI", PALETTE["violet"], "^", 1.8, 4.4, 2),
    )
    all_years: set[int] = set()
    indexed_series: dict[str, list[float]] = {}
    for indicator, color, marker, linewidth, markersize, zorder in styles:
        fact = fact_by_name.get(indicator)
        if fact is None:
            continue
        years, values = _series(fact)
        if not values or values[0] == 0:
            continue
        baseline = values[0]
        indexed = [100.0 * value / baseline for value in values]
        indexed_series[indicator] = indexed
        all_years.update(years)
        axis.plot(years, indexed, label=indicator, color=color, linewidth=linewidth, marker=marker, markersize=markersize, markeredgewidth=0, zorder=zorder)
    if {"NP", "PD"}.issubset(indexed_series) and len(indexed_series["NP"]) == len(indexed_series["PD"]) and all(
        abs(left - right) < 1e-9 for left, right in zip(indexed_series["NP"], indexed_series["PD"])
    ):
        axis.text(0.99, 0.035, "NP 与 PD 相对变化曲线重合", transform=axis.transAxes, ha="right", va="bottom", color=PALETTE["muted"], fontsize=8)
    axis.axhline(100, color=PALETTE["muted"], linewidth=0.8, linestyle=(0, (3, 3)), zorder=0)
    axis.set_xlabel("年份")
    axis.set_ylabel("相对指数（起始年 = 100）")
    if all_years:
        axis.set_xticks(sorted(all_years))
        axis.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.03), handlelength=2.2)
    else:
        axis.text(0.5, 0.5, "关键景观指标缺少可绘制时序", transform=axis.transAxes, ha="center", va="center", color=PALETTE["muted"])
    axis.margins(x=0.05, y=0.18)
    _finish_axis(axis)
    return _save_figure(figure, output)


def _matrix_chart(report: dict[str, Any], output: Path) -> dict[str, str]:
    rows = [item for item in report["problem_diagnoses"] if item.get("status") not in OMITTED_PROBLEM_STATUSES]
    rows.sort(key=lambda item: (0 if item.get("status") == "突出问题" else 1, item["problem_id"]))
    figure_height = max(2.4, 1.05 + 0.48 * len(rows))
    figure, axis = plt.subplots(figsize=(7.0, figure_height), constrained_layout=True)
    axis.set_xlim(0, 1)
    axis.set_ylim(0, len(rows) + 1)
    axis.axis("off")
    columns = ((0.00, 0.47, "生态问题"), (0.47, 0.67, "当前判断"), (0.67, 0.83, "严重程度"), (0.83, 1.00, "变化趋势"))
    for left, right, title in columns:
        axis.add_patch(Rectangle((left, len(rows)), right - left, 1, facecolor=PALETTE["header_fill"], edgecolor="white"))
        axis.text((left + right) / 2, len(rows) + 0.5, title, ha="center", va="center", fontweight="bold")
    for index, item in enumerate(rows):
        y = len(rows) - index - 1
        fill = PALETTE["prominent_fill"] if item.get("status") == "突出问题" else PALETTE["observation_fill"]
        for left, right, _ in columns:
            axis.add_patch(Rectangle((left, y), right - left, 1, facecolor=fill, edgecolor="white", linewidth=1.3))
        axis.text(0.015, y + 0.5, "\n".join(textwrap.wrap(item["problem_name"], width=18)), ha="left", va="center")
        axis.text(0.57, y + 0.5, item["status"], ha="center", va="center")
        axis.text(0.75, y + 0.5, SEVERITY_LABELS.get(item.get("severity", "unknown"), "未知"), ha="center", va="center")
        trend_color = PALETTE["red"] if item.get("trend") in {"worsening", "recently_worsening"} else PALETTE["ink"]
        axis.text(0.915, y + 0.5, TREND_LABELS.get(item.get("trend", "unknown"), "未知"), ha="center", va="center", color=trend_color)
    return _save_figure(figure, output)


def risk_chart_data(report: dict[str, Any]) -> dict[str, Any]:
    evidence = report.get("risk_evidence") or report.get("spatial_evidence", {}).get("risk_evidence") or {}
    annual = [
        {"year": int(item["year"]), "value": float(item["mean_ceri"]), "valid_scope_count": int(item.get("valid_scope_count", 0))}
        for item in evidence.get("annual_series", [])
        if item.get("mean_ceri") is not None
    ]
    gradient = []
    for item in evidence.get("spatial_gradient", []):
        value = (item.get("values") or {}).get("CERI")
        if value is not None:
            gradient.append({"scope": item.get("scope"), "value": float(value), "risk_level": item.get("risk_level")})
    return {
        "schema_version": 1,
        "title": "综合生态风险趋势与空间梯度",
        "annual_aggregation": "multi_scope_mean",
        "annual_series": annual,
        "target_year": int(evidence.get("target_year", report["report_metadata"]["current_year"])),
        "spatial_gradient": gradient,
        "source_scope": evidence.get("source_scope"),
    }


def _risk_chart(report: dict[str, Any], output: Path) -> dict[str, str]:
    data = risk_chart_data(report)
    figure, axes = plt.subplots(1, 2, figsize=(7.2, 3.5), constrained_layout=True)
    annual = data["annual_series"]
    if annual:
        years = [item["year"] for item in annual]
        values = [item["value"] for item in annual]
        axes[0].plot(years, values, color=PALETTE["red"], linewidth=1.8, marker="o", markersize=4, markeredgewidth=0)
        axes[0].set_xticks(years)
        axes[0].set_xlabel("年份")
        axes[0].set_ylabel("CERI")
    else:
        axes[0].text(0.5, 0.5, "缺少可用年度风险值", ha="center", va="center", color=PALETTE["muted"])
    gradient = data["spatial_gradient"]
    if gradient:
        axes[1].plot([item["scope"] for item in gradient], [item["value"] for item in gradient], color=PALETTE["violet"], linewidth=1.8, marker="o", markersize=4, markeredgewidth=0)
        axes[1].set_xlabel(f"空间范围（{data['target_year']}年）")
        axes[1].set_ylabel("CERI")
        axes[1].tick_params(axis="x", rotation=35)
    else:
        axes[1].text(0.5, 0.5, "缺少目标年空间风险值", ha="center", va="center", color=PALETTE["muted"])
    for axis in axes:
        _finish_axis(axis)
    return _save_figure(figure, output)


def build_report_charts(report: dict[str, Any], output_directory: str | Path) -> list[dict[str, Any]]:
    _configure_style()
    output = Path(output_directory)
    vegetation = output / "figure-2-1-vegetation.png"
    landscape = output / "figure-2-2-landscape.png"
    matrix = output / "figure-5-1-diagnosis-matrix.png"
    vegetation_paths = _vegetation_chart(report["indicator_facts"], vegetation)
    landscape_paths = _indexed_landscape_chart(report["indicator_facts"], landscape)
    matrix_paths = _matrix_chart(report, matrix)
    start_year = report["report_metadata"]["evaluation_start_year"]
    end_year = report["report_metadata"]["evaluation_end_year"]
    charts = [
        {"asset_id": "figure-2-1", "title": "关键植被指标时序图", "caption": f"图 2-1 {start_year}—{end_year}年NDVI与NPP变化趋势", **vegetation_paths, "dpi": 300},
        {"asset_id": "figure-2-2", "title": "关键景观指标时序图", "caption": f"图 2-2 {start_year}—{end_year}年关键景观格局指标变化趋势", **landscape_paths, "dpi": 300},
        {"asset_id": "figure-5-1", "title": "生态问题诊断矩阵", "caption": "图 5-1 突出问题与持续观察问题诊断矩阵", **matrix_paths, "dpi": 300},
    ]
    if report.get("risk_evidence") is not None:
        risk = output / "figure-2-3-risk.png"
        risk_paths = _risk_chart(report, risk)
        charts.insert(2, {"asset_id": "figure-2-3", "title": "综合生态风险趋势与空间梯度", "caption": f"图 2-3 {start_year}—{end_year}年多空间范围CERI均值及{end_year}年空间梯度", **risk_paths, "data_json": risk_chart_data(report), "dpi": 300})
    return charts
