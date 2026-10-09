from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .llm_http import request_chat_json


@dataclass(frozen=True)
class OpenAICompatibleWriterConfig:
    base_url: str
    model: str
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY"
    timeout_seconds: int = 240
    temperature: float = 0.25
    max_tokens: int = 16000
    checkpoint_directory: Path | None = None
    max_attempts: int = 4


_WRITER_SOURCE_INFERENCE_PATTERNS = (
    re.compile(
        r"[^。！？；\n]*可能[^。！？；\n]{0,40}(?:反映|由于|导致|生效|受限于|源于|归因于)[^。！？；\n]*[。！？；]?"
    ),
    re.compile(
        r"[^。！？；\n]*(?:通常反映|自然波动|局部环境因子|饱和点|污染压力|驱动机制|风险源解析|敏感性评估)[^。！？；\n]*[。！？；]?"
    ),
    re.compile(
        r"[^。！？；\n]*可能[^。！？；\n]{0,40}(?:处于|来自|受到|关联|扩散)[^。！？；\n]*[。！？；]?"
    ),
    re.compile(
        r"[^。！？；\n]*(?:排查|建议)[^。！？；\n]{0,30}(?:因子|原因|开展|监测|监控|干预|投入)[^。！？；\n]*[。！？；]?"
    ),
)


def _sanitize_writer_text(value: str) -> str:
    """Remove unsupported source-language scaffolding before it reaches the Writer."""
    text = re.sub(r"规则ID为PR-P\d+[，,]?\s*", "", value)
    text = re.sub(r"规则\s*(?:ID\s*)?为?\s*PR-P\d+\s*指定", "既定条件指定", text)
    text = re.sub(r"PR-P\d+", "", text)
    text = text.replace("问题规则指定的核心指标", "问题涉及的核心指标")
    text = text.replace("规则指定指标", "核心指标")
    text = text.replace("规则指定", "既定")
    text = text.replace("诊断规则", "诊断条件")
    text = text.replace("ca_landcover_type", "土地覆盖类型数据")
    text = text.replace("has_buffer_gradient", "缓冲区梯度数据")
    text = text.replace("historical_abandoned_mine", "历史遗留废弃矿区数据")
    text = text.replace("当前结论不可评估", "现有数据不足以形成该项判断")
    text = text.replace("关键条件不足", "必要证据不足")
    for pattern in _WRITER_SOURCE_INFERENCE_PATTERNS:
        text = pattern.sub("现有数据不足以判断具体原因。", text)
    return text


def _sanitize_writer_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, str):
        return _sanitize_writer_text(value)
    if isinstance(value, list):
        return [_sanitize_writer_value(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_key in {"rule_id"}:
                continue
            sanitized[child_key] = _sanitize_writer_value(child_value, child_key)
        return sanitized
    return value


def build_writer_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Expose only reportable, reader-facing semantics to the narrative model."""
    payload = deepcopy(report)
    omitted = {"数据不足", "不适用"}
    diagnoses = [item for item in payload.get("problem_diagnoses", []) if item.get("status") not in omitted]
    reportable_ids = {item["problem_id"] for item in diagnoses}
    for item in diagnoses:
        item.pop("provenance", None)
        item.pop("rule_id", None)
        diagnosis = item.get("diagnosis")
        if isinstance(diagnosis, dict):
            diagnosis.pop("rule_id", None)
        basis = item.get("analysis_context", {}).get("basis", {})
        if isinstance(basis, dict):
            basis.pop("rule_id", None)
    payload["problem_diagnoses"] = diagnoses
    measures = []
    for item in payload.get("measure_recommendations", []):
        targets = [problem_id for problem_id in item.get("target_problem_ids", []) if problem_id in reportable_ids]
        if not targets:
            continue
        item["target_problem_ids"] = targets
        item.pop("priority", None)
        measures.append(item)
    payload["measure_recommendations"] = measures
    payload.pop("priority_assessment", None)
    payload.pop("audit", None)
    payload.pop("report_assets", None)
    project = payload.get("project", {})
    if isinstance(project, dict):
        project.pop("project_attributes", None)
    return _sanitize_writer_value(payload)


def _counter_evidence_requirements(
    report: dict[str, Any], issues: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Carry verified source facts into counter-evidence corrections."""
    from .mvp_writer import INDICATOR_LABELS

    diagnoses = {
        item.get("problem_id"): item
        for item in report.get("problem_diagnoses", [])
        if isinstance(item, dict)
    }
    facts = {
        item.get("indicator"): item
        for item in report.get("indicator_facts", [])
        if isinstance(item, dict) and item.get("indicator")
    }
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.get("code") != "counter_evidence_omitted":
            continue
        problem_id = issue.get("message", "")
        if problem_id in seen or problem_id not in diagnoses:
            continue
        seen.add(problem_id)
        diagnosis = diagnoses[problem_id]
        synthesis = diagnosis.get("analysis_context", {}).get("indicator_synthesis", {})
        required_signals: list[dict[str, Any]] = []
        for signal in synthesis.get("conflicting_signals", []):
            if not isinstance(signal, dict):
                continue
            indicator = signal.get("indicator")
            fact = facts.get(indicator, {})
            statistics = fact.get("statistics") if isinstance(fact, dict) else {}
            trend = fact.get("trend") if isinstance(fact, dict) else {}
            statistics = statistics if isinstance(statistics, dict) else {}
            trend = trend if isinstance(trend, dict) else {}
            source = {
                "evidence_id": signal.get("evidence_id"),
                "fact_ref": signal.get("fact_ref"),
                "indicator": indicator,
                "indicator_label": INDICATOR_LABELS.get(indicator, indicator),
                "supports_problem": signal.get("supports_problem"),
                "long_term": signal.get("long_term"),
                "recent": signal.get("recent"),
                "turning_points": signal.get("turning_points", []),
            }
            for field in ("current_value", "current_year", "relative_slope", "recent_relative_change"):
                if field in statistics:
                    source[field] = statistics[field]
                elif field in trend:
                    source[field] = trend[field]
            quality = fact.get("quality") if isinstance(fact, dict) else {}
            if isinstance(quality, dict) and "valid_year_count" in quality:
                source["valid_year_count"] = quality["valid_year_count"]
            required_signals.append(source)
        requirements.append(
            {
                "problem_id": problem_id,
                "problem_name": diagnosis.get("problem_name"),
                "required_counter_evidence": required_signals,
                "instruction": (
                    f"在 problem_blocks 中保留 {problem_id} 的每条 required_counter_evidence；"
                    "正文必须逐一明确点名指标或指标名称，并说明其长期/近期趋势与该问题方向不一致、"
                    "构成反向证据或未支持证据。只能使用给出的事实，不得补造原因。"
                ),
            }
        )
    return requirements


def _unsupported_language_requirements(issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    fragments = [
        issue.get("message", "")
        for issue in issues
        if issue.get("code") == "unsupported_inference_or_action" and issue.get("message")
    ]
    if not fragments:
        return []
    return [
        {
            "offending_fragments": fragments,
            "forbidden_expressions": [
                "可能反映、可能由于、可能导致、可能生效、可能受限于、可能源于、可能归因于",
                "可能处于、可能来自、可能受到、可能关联",
                "通常反映、自然波动、局部环境因子、饱和点、污染压力、驱动机制、风险源解析、敏感性评估",
                "未受显著负面干扰",
                "排查因子/原因，以及建议开展/监测/监控/干预/投入",
            ],
            "action": (
                "在全稿中删除每个 offending_fragments 所在的无支撑句或从句；"
                "不得把‘可能’改成肯定因果，也不得补写机制、原因、调查、监测或措施。"
                "仅保留输入已证明的数值、趋势、空间差异和证据边界；完成全稿自检，"
                "确保 summary 与所有 sections 均不再出现 offending_fragments 或上述禁用表达。"
            ),
        }
    ]


def _machine_language_requirements(issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    fragments = [
        issue.get("message", "")
        for issue in issues
        if issue.get("code") == "machine_language" and issue.get("message")
    ]
    if not fragments:
        return []
    return [
        {
            "offending_fragments": fragments,
            "forbidden_expressions": [
                "支持状态为 True/False", "analysis_context", "supports_problem", "PR-P0至PR-P3",
                "historical_abandoned_mine", "ca_landcover_type", "has_buffer_gradient",
                "诊断规则", "规则指定", "关键条件未满足", "当前结论不可评估",
                "ReportIR", "Agent", "Writer", "JSON",
            ],
            "action": (
                "删除 offending_fragments 所在的机器字段、布尔值或内部术语，必要时删除整句；"
                "如仍需表达该事实，只能改写为输入已证明的自然中文数值、趋势、空间差异或证据边界。"
                "不得改变事实，不得把内部字段翻译成新的原因、机制、行动或肯定判断；完成全稿自检。"
            ),
        }
    ]


def _length_requirements(
    report: dict[str, Any], issues: list[dict[str, str]]
) -> list[dict[str, Any]]:
    diagnoses = {
        item.get("problem_id"): item
        for item in report.get("problem_diagnoses", [])
        if isinstance(item, dict) and item.get("problem_id")
    }
    requirements: list[dict[str, Any]] = []
    for issue in issues:
        code = issue.get("code")
        message = str(issue.get("message", ""))
        if code == "soft_length_range":
            match = re.match(r"^(?P<problem_id>[^:]+):(?P<current>\d+), recommended (?P<low>\d+)-(?P<high>\d+)$", message)
        elif code == "problem_analysis_too_shallow":
            match = re.match(r"^(?P<problem_id>[^:]+):(?P<current>\d+)<(?P<hard_floor>\d+)$", message)
        else:
            match = None
        if not match:
            continue
        problem_id = match.group("problem_id")
        diagnosis = diagnoses.get(problem_id)
        if diagnosis is None:
            continue
        prominent = diagnosis.get("status") == "突出问题"
        target_low, target_high = (1000, 1300) if prominent else (300, 450)
        source = {
            key: deepcopy(diagnosis.get(key))
            for key in (
                "problem_id", "problem_name", "status", "severity", "trend",
                "evidence_level", "confidence", "evidence", "analysis_features", "analysis_context",
            )
            if key in diagnosis
        }
        requirements.append(
            {
                "problem_id": problem_id,
                "current_length": int(match.group("current")),
                "target_length": {"min": target_low, "max": target_high},
                "source_diagnosis": source,
                "instruction": (
                    f"只扩写 {problem_id} 的 problem_blocks，目标长度为{target_low}至{target_high}字；"
                    "围绕完整源诊断事实补足证据形成、时间关系、空间差异、反向证据、不确定性和修复边界。"
                    "不得重复填充，不得改变数值或诊断，不得编造原因、现场事实、措施或工程参数。"
                ),
            }
        )
    return requirements
SYSTEM_PROMPT = """你是正式生态报告 Writer，不负责生态诊断。
输入是一份已经校验的、仅包含可报告问题的语义材料。所有指标事实、诊断状态、趋势、证据关系、措施和推理边界均已确定。
当 overall_assessment.risk_level_status 为 not_provided 时，不得生成、推断或暗示总体风险等级。
风险证据中的当前风险等级、来源空间范围、LER/ES/EP/RER/CVR/CERI构成、年度多空间范围均值趋势和目标年空间梯度必须保持原样；趋势聚合为多空间范围均值，不得改写为单一核心范围时序。significance_status 为 missing 时不得写“显著上升/显著下降”或其他统计显著性结论。
当 audit 已被过滤且 measure_recommendations 为空时，只能说明未提供项目候选措施，不得推荐任何具体措施。
请输出一个 JSON ReportDraft，顶层只包含 summary 和 sections。
summary={paragraphs:[字符串],keywords:[3至6个短语]}。
sections 必须恰好包含 overall_assessment、problem_analysis、cross_problem、restoration、conclusion 五种 section_id，每项包含 section_id、title、paragraphs；problem_analysis 额外包含 problem_blocks。
problem_blocks 必须覆盖输入中的全部问题且每个只出现一次；每项包含 problem_id、title、paragraphs。先写“突出问题”，再写“需要持续观察的潜在问题”；同组内按问题本身形成自然叙事，不解释排序依据。
不要逐字段机械输出，不要让不同问题采用同一段落结构。应根据已有分析素材自主选择长期/近期关系、多指标支持或冲突、生态含义、不确定性和修复含义中最有解释价值的内容，并跨段去重。
突出问题字数目标1000至1300字，持续观察问题字数目标300至450字；分析密度优先。突出问题必须解释诊断如何由证据形成、保留反向证据、说明边界及修复含义。
如果 conflicting_signals 非空，正文必须逐一明确点名其中的反向指标，并用“但、然而、不一致、未支持”等转折说明其为何限制结论强度。
不得改变任何数值、趋势、诊断、confidence 和证据关系；不得新增问题、措施、现场事实、因果关系或工程参数；不得删除重要反向证据。
正文必须像专业技术人员撰写的正式报告，不得提及 ReportIR、Agent、Writer、JSON、分析框架、字段名、规则编号、原始条件名、True/False、机器标签、提示词或生成过程。禁止出现“诊断规则要求”“关键条件未满足”“当前结论不可评估”“归入P0级”等系统判定话术，也不得出现任何优先级、评分或P0至P3表述。
输入未包含数据不足或不适用问题；不得推测、补写或提及这些问题。
不得使用机制推测、原因假设或行动性表达补造原因，也不得新增监测、监控、干预、投入或调查行动。无法解释的现象只能写“现有数据不足以判断具体原因”，不能写成因果判断。
这是输出前的硬约束：最终 JSON 的任何字符串中都不得出现字面词“可能”或“建议”，不得出现机制推测、上述内部字段、规则编号或行动性字面表达；即使是在引用、反例或解释提示中也必须删除或改写为自然的事实边界，所有不确定性统一采用“现有数据不足以判断具体原因”的边界句。
修复部分只能复述 measure_recommendations 中已经合规匹配的措施及其既有边界；某问题没有匹配措施时，只能说明尚无合规匹配，不得自行提出行动。
只返回 JSON 对象，不要返回 Markdown。"""

LEXICAL_CLEANUP_SYSTEM_PROMPT = """你只执行严格的词法清理，不重写事实。
输入包含上一版完整 ReportDraft、校验命中的违规表达类别和必要片段。返回同结构、同问题、同数值的完整 JSON ReportDraft。
只允许删除违规表达所在的完整句或从句；不得新增、改写或推断任何其他事实、趋势、措施、原因或行动。
最终 JSON 的所有字符串不得出现字面词“可能”或“建议”，不得出现内部字段、规则编号或机器术语；解释边界只能写“现有数据不足以判断具体原因”。
提交前逐字符自检后只返回 JSON，不要返回 Markdown。"""


def _cacheable_report_draft(report: dict[str, Any], draft: dict[str, Any]) -> bool:
    from .mvp_writer import validate_narrative_draft

    try:
        return not validate_narrative_draft(draft, report)
    except (KeyError, TypeError, ValueError):
        return False


class OpenAICompatibleNarrativeBackend:
    name = "llm"

    def __init__(self, config: OpenAICompatibleWriterConfig):
        self.config = config
        self.request_count = 0
        self.logical_request_count = 0
        self.retry_count = 0
        self.request_durations_seconds: list[float] = []

    def _request(
        self,
        messages: list[dict[str, str]],
        label: str,
        cache_validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        print(f"[writer] requesting {label}", flush=True)
        result, attempts, durations, cached = request_chat_json(
            base_url=self.config.base_url,
            model=self.config.model,
            api_key_env=self.config.api_key_env,
            timeout_seconds=self.config.timeout_seconds,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=messages,
            label=label,
            checkpoint_directory=self.config.checkpoint_directory,
            max_attempts=self.config.max_attempts,
            cache_validator=cache_validator,
        )
        self.request_count += attempts
        if not cached:
            self.logical_request_count += 1
            self.retry_count += max(0, attempts - 1)
        self.request_durations_seconds.extend(durations)
        print(f"[writer] completed {label}", flush=True)
        return result

    def __call__(self, report: dict[str, Any], _baseline: str) -> dict[str, Any]:
        return self._request(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(build_writer_payload(report), ensure_ascii=False)},
            ],
            "ReportDraft",
            cache_validator=lambda draft: _cacheable_report_draft(report, draft),
        )

    def revise(self, report: dict[str, Any], draft: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
        issue_codes = {issue.get("code") for issue in issues}
        if issue_codes and issue_codes <= {"unsupported_inference_or_action", "machine_language"}:
            lexical_request = {
                "task": "对 current_draft 做最小词法清理",
                "current_draft": draft,
                "issues": [
                    {"code": issue.get("code"), "offending_fragment": issue.get("message", "")}
                    for issue in issues
                ],
                "requirements": [
                    "只删除违规表达所在的完整句或从句，保留其余 JSON 结构、问题ID、数值、趋势、证据关系和措施边界",
                    "不得新增或改写其他事实、原因、机制、行动或工程参数",
                    "最终所有字符串逐字符检查，不得出现字面词‘可能’或‘建议’、内部术语或规则编号",
                ],
            }
            return self._request(
                [
                    {"role": "system", "content": LEXICAL_CLEANUP_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(lexical_request, ensure_ascii=False)},
                ],
                "lexical cleanup ReportDraft",
                cache_validator=lambda corrected: _cacheable_report_draft(report, corrected),
            )
        correction = {
            "task": "修正上一版 ReportDraft",
            "validation_issues": issues,
            "counter_evidence_requirements": _counter_evidence_requirements(report, issues),
            "unsupported_language_requirements": _unsupported_language_requirements(issues),
            "machine_language_requirements": _machine_language_requirements(issues),
            "length_requirements": _length_requirements(report, issues),
            "requirements": [
                "只修正列出的问题，不改变输入中的任何事实、诊断、证据关系或措施边界",
                "不得通过新增原因假设、现场事实、措施、调查、监测或工程参数来扩写",
                "若问题是篇幅不足，必须按 length_requirements 对指定问题扩写到目标范围，只能深化既有证据之间的关系、反向证据、时间尺度和不确定性",
                "清除规则编号、字段名、系统判定话术以及优先级或P0至P3表述",
                "提交前逐字符检查完整 JSON 的所有字符串：不得出现字面词‘可能’或‘建议’，不得新增任何行动；解释边界统一写‘现有数据不足以判断具体原因’",
                "返回完整且结构合规的 JSON ReportDraft",
            ],
        }
        return self._request(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(build_writer_payload(report), ensure_ascii=False)},
                {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
            ],
            "corrected ReportDraft",
            cache_validator=lambda corrected: _cacheable_report_draft(
                report, corrected
            ),
        )
