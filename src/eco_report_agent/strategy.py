from __future__ import annotations

from .models import MeasureDecision, ProblemDiagnosis, SelectedMeasure


PROBLEM_MEASURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "工程扰动引发植被退化": ("植被", "灌草", "补播", "种子库", "覆绿", "群落", "封禁"),
    "生态系统固碳能力下降": ("固碳", "碳基质", "有机堆肥"),
    "原生草原面积缩减": ("草原", "灌草", "补播", "种子库", "封禁"),
    "景观破碎化": ("关键节点", "廊道", "防护林带", "分区土地复垦", "近自然"),
    "景观边缘效应": ("防护林带", "风蚀", "边坡", "水土流失"),
    "土地沙化": ("耐旱", "防护林带", "风蚀", "覆绿", "覆盖", "水土流失"),
    "生态修复成效": ("动态监测", "恢复能力", "复盘", "生态监测"),
    "栖息地完整性受损": ("廊道", "关键节点", "近自然", "封禁", "分区土地复垦"),
    "复杂人为镶嵌景观": ("分区", "关键节点", "近自然", "土地复垦"),
    "光伏治沙双向效应": ("光伏板间", "耐旱", "灌草", "防风", "沙化"),
    "历史遗留废弃矿区损害": ("采坑", "塌陷坑", "矸石", "尾矿", "土地复垦", "地形规整"),
    "综合生态风险": ("分区差异化", "协同修复", "长效管控", "数字化动态监测"),
}

ELIGIBLE_STATUSES = {"突出问题", "存在但改善中", "存在风险"}


def select_measures(
    diagnoses: list[ProblemDiagnosis],
    measure_audit: list[MeasureDecision],
    max_per_problem: int = 3,
) -> list[SelectedMeasure]:
    accepted = [item for item in measure_audit if item.accepted]
    selected: list[SelectedMeasure] = []
    used: set[str] = set()
    for diagnosis in diagnoses:
        if diagnosis.status not in ELIGIBLE_STATUSES:
            continue
        keywords = PROBLEM_MEASURE_KEYWORDS.get(diagnosis.problem, ())
        count = 0
        for measure in accepted:
            if measure.text in used:
                continue
            matched = next((keyword for keyword in keywords if keyword in measure.text), None)
            if not matched:
                continue
            selected.append(
                SelectedMeasure(
                    problem=diagnosis.problem,
                    text=measure.text,
                    match_type=f"keyword:{matched}",
                    source=measure.source,
                )
            )
            used.add(measure.text)
            count += 1
            if count >= max_per_problem:
                break
    return selected
