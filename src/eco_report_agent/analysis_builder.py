from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


LONG_FEATURES = {
    "overall_up": "long_term_deterioration",
    "fluctuating_upward": "long_term_deterioration",
    "overall_down": "long_term_improvement",
    "fluctuating_downward": "long_term_improvement",
    "stable": "long_term_stable",
    "rise_then_fall": "mixed_long_term_pattern",
    "fall_then_rise": "mixed_long_term_pattern",
}
RECENT_FEATURES = {
    "up": "recent_deterioration",
    "down": "recent_improvement",
    "stable": "recent_stable",
}
ALLOWED_ANALYSIS_FEATURES = {
    "data_limitation", "scope_limitation", "strong_direct_evidence",
    "moderate_direct_evidence", "weak_direct_evidence", "indicator_consistency",
    "partial_consistency", "indicator_conflict", "long_term_deterioration", "long_term_improvement",
    "long_term_stable", "mixed_long_term_pattern", "recent_deterioration",
    "recent_improvement", "recent_stable", "recent_reversal",
    "cross_dimension_contrast", "shared_signal", "restoration_relevance",
}
FORBIDDEN_GENERATED_PATTERNS = (
    re.compile(r"可能.{0,40}(?:反映|由于|导致|生效|受限于|源于|归因于)"),
    re.compile(r"可能.{0,40}(?:处于|来自|受到|关联)"),
    re.compile(r"通常反映|自然波动|局部环境因子|饱和点|污染压力"),
    re.compile(r"未受显著负面干扰"),
    re.compile(r"风险源解析|敏感性评估|排查.{0,20}(?:因子|原因)|建议.{0,30}(?:开展|监测|监控|干预|投入)"),
)


def _strength(count: int, total: int) -> str:
    if count <= 0:
        return "none"
    ratio = count / max(total, 1)
    return "strong" if ratio >= 0.75 else "moderate" if ratio >= 0.5 else "weak"


def _relationship(value: str) -> str:
    return {
        "consistent": "consistent",
        "partially_consistent": "mostly_consistent",
        "conflicting": "conflicting",
        "insufficient": "insufficient",
    }.get(value, "mixed")


def build_analysis_contexts(
    diagnoses: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    analysis_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a fact-locked AnalysisContext seed without revisiting diagnosis."""
    fact_map = {item["indicator"]: item for item in facts}
    measure_map: dict[str, list[dict[str, Any]]] = {}
    for measure in measures:
        for problem_id in measure["target_problem_ids"]:
            measure_map.setdefault(problem_id, []).append(measure)
    dimensions = analysis_config["indicator_dimensions"]
    dimension_labels = analysis_config["dimension_labels"]
    enriched: list[dict[str, Any]] = []
    for original in diagnoses:
        item = deepcopy(original)
        problem_id = item["problem_id"]
        evidence = item["evidence"]
        features: set[str] = set()
        if item["data_sufficiency"] == "insufficient":
            features.add("data_limitation")
        if item["applicability"] == "not_applicable":
            features.add("scope_limitation")
        if item["confidence"] == "high":
            features.add("strong_direct_evidence")
        elif item["confidence"] == "medium":
            features.add("moderate_direct_evidence")
        elif item["confidence"] == "low":
            features.add("weak_direct_evidence")
        relationship = _relationship(item["evidence_consistency"])
        features.add({
            "consistent": "indicator_consistency",
            "mostly_consistent": "partial_consistency",
            "mixed": "partial_consistency",
            "conflicting": "indicator_conflict",
            "insufficient": "data_limitation",
        }[relationship])
        signals: list[dict[str, Any]] = []
        long_patterns: set[str] = set()
        recent_patterns: set[str] = set()
        for evidence_item in evidence:
            fact = fact_map[evidence_item["indicator"]]
            long_term = fact["trend"]["long_term"]
            recent = fact["trend"]["recent"]
            long_patterns.add(long_term)
            recent_patterns.add(recent)
            if long_term in LONG_FEATURES:
                features.add(LONG_FEATURES[long_term])
            if recent in RECENT_FEATURES:
                features.add(RECENT_FEATURES[recent])
            signals.append({
                "evidence_id": evidence_item["evidence_id"],
                "indicator": evidence_item["indicator"],
                "fact_ref": evidence_item["fact_ref"],
                "long_term": long_term,
                "recent": recent,
                "turning_points": list(fact["trend"].get("turning_points", [])),
                "supports_problem": evidence_item["supports_problem"],
            })
        if item["trend"] in {"recently_improving", "recently_worsening"}:
            features.add("recent_reversal")
        support = [signal for signal in signals if signal["supports_problem"] is True]
        counter = [signal for signal in signals if signal["supports_problem"] is False]
        stable = [signal for signal in signals if signal["long_term"] == "stable" or signal["recent"] == "stable"]
        support_count = len(support)
        counter_count = len(counter)
        total = len(signals)
        if support_count and counter_count:
            overall_balance = "mixed"
        elif support_count == total and total:
            overall_balance = "strongly_supports_diagnosis"
        elif support_count:
            overall_balance = "supports_diagnosis"
        elif item["data_sufficiency"] == "insufficient":
            overall_balance = "insufficient"
        else:
            overall_balance = "does_not_support_diagnosis"
        if not long_patterns or not recent_patterns:
            long_recent_relation = "unknown"
        elif item["trend"] in {"recently_improving", "recently_worsening"}:
            long_recent_relation = "divergent"
        elif len(recent_patterns) > 1:
            long_recent_relation = "partially_divergent"
        else:
            long_recent_relation = "consistent"
        problem_dimensions = sorted({dimensions[name] for name in item["supporting_indicators"] if name in dimensions})
        matched = measure_map.get(problem_id, [])
        uncertainty = {
            "data_limitations": list(item["limitations"]),
            "interpretation_limitations": [] if item["status"] in {"数据不足", "不适用"} else ["现有指标只能支持结构化生态信号判断，不能识别具体驱动因素。"],
            "unsupported_inferences": ["不得据此推断具体工程活动、施工位置或现场成因。"],
            "confidence_reason": "关键条件不足，当前结论不可评估。" if item["status"] in {"数据不足", "不适用"} else f"结论基于{total}项规则指定指标证据。",
        }
        item["diagnosis"] = {
            "status": item["status"],
            "existence": item["existence"],
            "severity": item["severity"],
            "trend": item["trend"],
            "confidence": item["confidence"],
            "evidence_level": item["evidence_level"],
            "evidence_consistency": item["evidence_consistency"],
        }
        item["analysis_features"] = sorted(features)
        item["analysis_context"] = {
            "basis": {
                "core_indicators": list(item["supporting_indicators"]),
                "supporting_indicators": [signal["indicator"] for signal in support],
                "rule_id": item["provenance"]["rule_id"],
                "basis_summary": "仅使用问题规则指定指标及其可追溯证据形成判断。",
            },
            "temporal": {
                "indicator_patterns": signals,
                "long_term_patterns": sorted(long_patterns),
                "recent_patterns": sorted(recent_patterns),
                "turning_points": sorted({year for signal in signals for year in signal["turning_points"]}),
                "long_recent_relation": long_recent_relation,
                "summary": "长期和近期变化关系需结合逐指标证据解释。",
            },
            "indicator_synthesis": {
                "relationship": relationship,
                "dominant_indicators": [signal["indicator"] for signal in support],
                "supporting_evidence_ids": [signal["evidence_id"] for signal in support],
                "conflicting_evidence_ids": [signal["evidence_id"] for signal in counter],
                "stable_evidence_ids": [signal["evidence_id"] for signal in stable],
                "supporting_signals": support,
                "conflicting_signals": counter,
                "stable_signals": stable,
                "synthesis": "支持、反向和稳定信号必须同时保留并综合解释。",
            },
            "evidence_balance": {
                "support_strength": _strength(support_count, total),
                "counter_evidence_strength": _strength(counter_count, total),
                "direct_evidence": bool(signals),
                "risk_label_support": item["evidence_level"] == "B",
                "overall_balance": overall_balance,
                "summary": "证据强度由既定证据关系确定，不得由写作阶段提升。",
            },
            "interpretation": {
                "primary_dimensions": problem_dimensions,
                "dimension_labels": [dimension_labels[name] for name in problem_dimensions],
                "primary_finding": f"当前判断主要反映{'、'.join(dimension_labels[name] for name in problem_dimensions) or '规则指定生态维度'}的变化信号。",
                "secondary_findings": [],
                "ecological_meaning": "不同维度的证据必须分别保留，不将局部信号扩展为整体生态状态事实。",
            },
            "uncertainty": uncertainty,
            "restoration": {
                "matched_measure_ids": [measure["measure_id"] for measure in matched],
                "matched_measures": [
                    {"measure_id": measure["measure_id"], "name": measure["name"], "mechanism": measure["mechanism"]}
                    for measure in matched
                ],
                "direction": "仅解释已匹配措施与诊断之间的关系，不扩展为工程设计。" if matched else "现有候选措施未形成合规匹配，不补造修复方向。",
                "implementation_limits": ["现有数据不足以确定具体位置、工程尺度和实施参数。"],
                "match_status": "matched" if matched else "no_compliant_match",
            },
        }
        enriched.append(item)
    return enriched


def build_cross_problem_analysis(
    diagnoses: list[dict[str, Any]],
    analysis_config: dict[str, Any],
) -> dict[str, Any]:
    by_indicator: dict[str, list[str]] = {}
    dimension_weight: dict[str, float] = {}
    dimension_signals: dict[str, dict[str, int]] = {}
    for diagnosis in diagnoses:
        if diagnosis["status"] in {"数据不足", "不适用"}:
            continue
        for indicator in diagnosis["supporting_indicators"]:
            by_indicator.setdefault(indicator, []).append(diagnosis["problem_id"])
        for dimension in diagnosis["analysis_context"]["interpretation"]["primary_dimensions"]:
            profile = dimension_signals.setdefault(dimension, {"supporting": 0, "conflicting": 0})
            synthesis = diagnosis["analysis_context"]["indicator_synthesis"]
            profile["supporting"] += len(synthesis["supporting_evidence_ids"])
            profile["conflicting"] += len(synthesis["conflicting_evidence_ids"])
            dimension_weight[dimension] = dimension_weight.get(dimension, 0.0) + max(
                1, len(synthesis["supporting_evidence_ids"]) + len(synthesis["conflicting_evidence_ids"])
            )
    shared = [
        {
            "indicators": [indicator],
            "related_problem_ids": sorted(problem_ids),
            "interpretation": "该指标被多个问题规则共同引用，具体含义需在跨问题层区分。",
        }
        for indicator, problem_ids in sorted(by_indicator.items())
        if len(problem_ids) > 1
    ]
    dominant = max(dimension_weight, key=dimension_weight.get) if dimension_weight else None
    labels = analysis_config["dimension_labels"]
    return {
        "shared_signals": shared,
        "contrasting_dimensions": [],
        "dimension_profiles": [
            {
                "dimension": dimension,
                "label": labels.get(dimension, dimension),
                "supporting_signal_count": values["supporting"],
                "conflicting_signal_count": values["conflicting"],
            }
            for dimension, values in sorted(dimension_signals.items())
        ],
        "dominant_ecological_dimension": dominant,
        "dominant_dimension_label": labels.get(dominant) if dominant else None,
        "integrated_interpretation": "综合解释应识别跨问题共同信号、维度差异和当前主要生态矛盾。",
        "uncertainty": "跨问题综合不改变任何单问题诊断或证据强度。",
    }


ANALYSIS_SYSTEM_PROMPT = """你是生态报告 Analysis Builder，不是诊断 Agent，也不是正式报告 Writer。
输入中的 diagnosis、IndicatorFact、ProblemEvidence 和措施均已锁定。只输出 JSON 对象，包含 problems 和 cross_problem_analysis。
每个 problems 项只包含 problem_id、analysis_features、analysis_context，并保留 seed 的完整字段结构。
所有 ID、指标、趋势、证据支持关系、措施 ID 和匹配状态必须逐字逐值保持不变。
你可以改进各 summary、interpretation、uncertainty 和 restoration.direction，使其成为有信息密度的结构化分析素材。
必须同时保留支持与反向证据；不得增加指标事实、问题、原因、现场状态、工程参数或措施；不得写正式报告正文。
cross_problem_analysis 应识别共同信号、对立维度、主导生态维度和主要矛盾，但不得改变单问题判断。只返回 JSON，不要 Markdown。"""

PROBLEM_ANALYSIS_PROMPT = ANALYSIS_SYSTEM_PROMPT + """
本次只处理一个生态问题。输出顶层只包含 problem。
problem 只包含 problem_id、analysis_features、analysis_context。
analysis_context 只返回以下可生成字段，不要复制锁定事实：
basis.basis_summary；temporal.summary；indicator_synthesis.synthesis；evidence_balance.summary；
interpretation.primary_finding、secondary_findings、ecological_meaning；
uncertainty.interpretation_limitations、confidence_reason。
不得提出原因假设、驱动因子、现场调查、风险源解析、监测、排查或其他新增行动。修复方向由程序根据已匹配措施生成。"""

CROSS_ANALYSIS_PROMPT = """你是生态报告的 CrossProblem Analysis Builder。
输入中的每个单问题诊断、证据、analysis_features、analysis_context 和 dimension_profiles 均已锁定。
只输出 JSON：{\"cross_problem_analysis\": {...}}。
cross_problem_analysis 只包含 shared_signals、contrasting_dimensions、dominant_ecological_dimension、integrated_interpretation。
shared_signals 只能引用输入已有指标和问题 ID。
补充 contrasting_dimensions，识别主导生态维度并形成有项目针对性的 integrated_interpretation。
不得改变单问题结论，不得增加原因、现场事实、指标、问题、措施或工程参数。不要输出 Markdown。"""


@dataclass(frozen=True)
class OpenAICompatibleAnalysisConfig:
    base_url: str
    model: str
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY"
    timeout_seconds: int = 180
    temperature: float = 0.1
    max_tokens: int = 10000


class OpenAICompatibleAnalysisBackend:
    name = "llm"

    def __init__(self, config: OpenAICompatibleAnalysisConfig):
        self.config = config
        self.fallback_problem_ids: list[str] = []
        self.cross_fallback = False
        self.request_count = 0
        self.request_durations_seconds: list[float] = []

    def _request(self, payload: dict[str, Any], prompt: str, max_tokens: int | None = None) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"missing API key environment variable: {self.config.api_key_env}")
        endpoint = self.config.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        self.request_count += 1
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"analysis API HTTP {exc.code}: {detail}") from exc
        finally:
            self.request_durations_seconds.append(round(time.perf_counter() - started, 3))
        content = result["choices"][0]["message"]["content"]
        return content if isinstance(content, dict) else json.loads(content)

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.fallback_problem_ids = []
        self.cross_fallback = False
        self.request_count = 0
        self.request_durations_seconds = []
        fact_map = {item["indicator"]: item for item in payload["indicator_facts"]}
        rule_map = {item["problem_id"]: item for item in payload["problem_rules"]}
        generated = []
        for problem in payload["problems"]:
            if problem["applicability"] != "applicable" or problem["data_sufficiency"] != "sufficient":
                generated.append({
                    "problem_id": problem["problem_id"],
                    "analysis_features": problem["analysis_features"],
                    "analysis_context": problem["analysis_context"],
                })
                continue
            indicators = problem["analysis_context"]["basis"]["core_indicators"]
            unit = {
                "project": payload["project"],
                "indicator_rules": payload["indicator_rules"],
                "indicator_facts": [fact_map[name] for name in indicators if name in fact_map],
                "problem_rule": rule_map[problem["problem_id"]],
                "problem": problem,
            }
            print(f"[analysis-builder] requesting {problem['problem_id']}", flush=True)
            result = self._request(unit, PROBLEM_ANALYSIS_PROMPT)
            raw_problem = result.get("problem", result)
            try:
                merged = merge_problem_analysis(problem, raw_problem)
            except ValueError as exc:
                correction = dict(unit)
                correction["rejected_response"] = raw_problem
                correction["validation_error"] = str(exc)
                result = self._request(correction, PROBLEM_ANALYSIS_PROMPT + "\n上一次输出未通过校验。请仅修正指出的问题，不扩大事实边界。")
                try:
                    merged = merge_problem_analysis(problem, result.get("problem", result))
                except ValueError:
                    self.fallback_problem_ids.append(problem["problem_id"])
                    merged = {
                        "problem_id": problem["problem_id"],
                        "analysis_features": deepcopy(problem["analysis_features"]),
                        "analysis_context": deepcopy(problem["analysis_context"]),
                    }
                    print(f"[analysis-builder] boundary fallback {problem['problem_id']}", flush=True)
            generated.append(merged)
            print(f"[analysis-builder] completed {problem['problem_id']}", flush=True)
        cross_input = {
            "project": payload["project"],
            "problems": [
                {
                    "problem_id": item["problem_id"],
                    "problem_name": next(problem["problem_name"] for problem in payload["problems"] if problem["problem_id"] == item["problem_id"]),
                    "diagnosis": next(problem["diagnosis"] for problem in payload["problems"] if problem["problem_id"] == item["problem_id"]),
                    "analysis_features": item["analysis_features"],
                    "analysis_context": item["analysis_context"],
                }
                for item in generated
            ],
            "cross_problem_analysis": payload["cross_problem_analysis"],
        }
        print("[analysis-builder] requesting cross_problem_analysis", flush=True)
        cross_result = self._request(cross_input, CROSS_ANALYSIS_PROMPT, max_tokens=4000)
        raw_cross = cross_result.get("cross_problem_analysis", cross_result)
        try:
            cross = merge_cross_analysis(payload["cross_problem_analysis"], raw_cross)
        except ValueError as exc:
            correction = dict(cross_input)
            correction["rejected_response"] = raw_cross
            correction["validation_error"] = str(exc)
            cross_result = self._request(correction, CROSS_ANALYSIS_PROMPT + "\n上一次输出未通过校验。请仅修正结构和引用，不新增事实。", max_tokens=4000)
            try:
                cross = merge_cross_analysis(payload["cross_problem_analysis"], cross_result.get("cross_problem_analysis", cross_result))
            except ValueError:
                self.cross_fallback = True
                cross = deepcopy(payload["cross_problem_analysis"])
                print("[analysis-builder] boundary fallback cross_problem_analysis", flush=True)
        print("[analysis-builder] completed cross_problem_analysis", flush=True)
        return {
            "problems": generated,
            "cross_problem_analysis": cross,
        }


def merge_problem_analysis(seed: dict[str, Any], generated: Any) -> dict[str, Any]:
    if not isinstance(generated, dict) or set(generated) != {"problem_id", "analysis_features", "analysis_context"}:
        raise ValueError("invalid generated problem analysis structure")
    if generated["problem_id"] != seed["problem_id"]:
        raise ValueError("Analysis Builder changed problem_id")
    features = generated["analysis_features"]
    if not isinstance(features, list) or not features or not set(features).issubset(ALLOWED_ANALYSIS_FEATURES):
        raise ValueError("Analysis Builder returned invalid analysis_features")
    patch = generated["analysis_context"]
    expected = {
        "basis": {"basis_summary"},
        "temporal": {"summary"},
        "indicator_synthesis": {"synthesis"},
        "evidence_balance": {"summary"},
        "interpretation": {"primary_finding", "secondary_findings", "ecological_meaning"},
        "uncertainty": {"interpretation_limitations", "confidence_reason"},
    }
    if not isinstance(patch, dict) or not set(expected).issubset(patch) or not set(patch).issubset(set(expected) | {"restoration"}):
        raise ValueError("Analysis Builder returned invalid analysis_context sections")
    for section, fields in expected.items():
        if not isinstance(patch[section], dict) or set(patch[section]) != fields:
            raise ValueError(f"Analysis Builder returned invalid fields for {section}")
    context = deepcopy(seed["analysis_context"])
    for section in ("basis", "temporal", "indicator_synthesis", "evidence_balance"):
        value = next(iter(patch[section].values()))
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Analysis Builder returned empty {section} explanation")
        context[section].update(patch[section])
    interpretation = patch["interpretation"]
    if isinstance(interpretation["secondary_findings"], str):
        interpretation["secondary_findings"] = [interpretation["secondary_findings"]]
    if not isinstance(interpretation["primary_finding"], str) or not isinstance(interpretation["ecological_meaning"], str) or not isinstance(interpretation["secondary_findings"], list):
        raise ValueError("Analysis Builder returned invalid interpretation")
    context["interpretation"].update(interpretation)
    uncertainty = patch["uncertainty"]
    if isinstance(uncertainty["interpretation_limitations"], str):
        uncertainty["interpretation_limitations"] = [uncertainty["interpretation_limitations"]]
    if not isinstance(uncertainty["interpretation_limitations"], list) or not isinstance(uncertainty["confidence_reason"], str):
        raise ValueError("Analysis Builder returned invalid uncertainty")
    context["uncertainty"].update(uncertainty)
    guarded_text = json.dumps({name: patch[name] for name in ("basis", "temporal", "indicator_synthesis", "evidence_balance", "interpretation")}, ensure_ascii=False)
    for pattern in FORBIDDEN_GENERATED_PATTERNS:
        match = pattern.search(guarded_text)
        if match:
            raise ValueError(f"Analysis Builder introduced unsupported inference or action: {match.group(0)}")
    return {"problem_id": seed["problem_id"], "analysis_features": features, "analysis_context": context}


def merge_cross_analysis(seed: dict[str, Any], generated: Any) -> dict[str, Any]:
    required = {"shared_signals", "contrasting_dimensions", "dominant_ecological_dimension", "integrated_interpretation"}
    if not isinstance(generated, dict) or not required.issubset(generated):
        raise ValueError("invalid generated cross-problem analysis structure")
    selected = {name: deepcopy(generated[name]) for name in required}
    if not isinstance(selected["shared_signals"], list) or not isinstance(selected["contrasting_dimensions"], list) or not isinstance(selected["integrated_interpretation"], str):
        raise ValueError("invalid generated cross-problem analysis values")
    for shared in selected["shared_signals"]:
        if isinstance(shared, dict) and "indicators" not in shared and "indicator" in shared:
            shared["indicators"] = [shared.pop("indicator")]
    result = deepcopy(seed)
    result.update(selected)
    labels = {item["dimension"]: item["label"] for item in seed["dimension_profiles"]}
    dominant = selected["dominant_ecological_dimension"]
    if dominant not in labels and dominant is not None:
        raise ValueError("unknown dominant ecological dimension")
    result["dominant_dimension_label"] = labels.get(dominant)
    guarded_text = json.dumps(selected, ensure_ascii=False)
    for pattern in FORBIDDEN_GENERATED_PATTERNS:
        match = pattern.search(guarded_text)
        if match:
            raise ValueError(f"Analysis Builder introduced unsupported cross-problem inference or action: {match.group(0)}")
    return result


def build_analysis_payload(
    project: dict[str, Any],
    diagnoses: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    analysis_config: dict[str, Any],
    cross_problem_analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project": project,
        "indicator_rules": analysis_config,
        "indicator_facts": facts,
        "problem_rules": rules,
        "problems": [
            {
                "problem_id": item["problem_id"],
                "problem_name": item["problem_name"],
                "applicability": item["applicability"],
                "data_sufficiency": item["data_sufficiency"],
                "diagnosis": item["diagnosis"],
                "problem_evidence": item["evidence"],
                "limitations": item["limitations"],
                "analysis_features": item["analysis_features"],
                "analysis_context": item["analysis_context"],
            }
            for item in diagnoses
        ],
        "cross_problem_analysis": cross_problem_analysis,
    }


def _locked_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "basis": {name: context["basis"][name] for name in ("core_indicators", "supporting_indicators", "rule_id")},
        "temporal": {name: context["temporal"][name] for name in ("indicator_patterns", "long_term_patterns", "recent_patterns", "turning_points", "long_recent_relation")},
        "indicator_synthesis": {name: context["indicator_synthesis"][name] for name in (
            "relationship", "dominant_indicators", "supporting_evidence_ids", "conflicting_evidence_ids",
            "stable_evidence_ids", "supporting_signals", "conflicting_signals", "stable_signals",
        )},
        "evidence_balance": {name: context["evidence_balance"][name] for name in (
            "support_strength", "counter_evidence_strength", "direct_evidence", "risk_label_support", "overall_balance",
        )},
        "interpretation": {name: context["interpretation"][name] for name in ("primary_dimensions", "dimension_labels")},
        "uncertainty": {name: context["uncertainty"][name] for name in ("data_limitations", "unsupported_inferences")},
        "restoration": {name: context["restoration"][name] for name in ("matched_measure_ids", "matched_measures", "implementation_limits", "match_status")},
    }


def validate_analysis_draft(draft: Any, seeds: list[dict[str, Any]], seed_cross: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(draft, dict):
        return [{"code": "invalid_analysis_type", "message": type(draft).__name__}]
    issues: list[dict[str, str]] = []
    if set(draft) != {"problems", "cross_problem_analysis"} or not isinstance(draft.get("problems"), list):
        return [{"code": "invalid_analysis_top_level", "message": "expected problems and cross_problem_analysis"}]
    seed_map = {item["problem_id"]: item for item in seeds}
    generated = {item.get("problem_id"): item for item in draft["problems"] if isinstance(item, dict)}
    if set(generated) != set(seed_map) or len(generated) != len(draft["problems"]):
        return [{"code": "analysis_problem_mismatch", "message": "problem ids changed"}]
    context_keys = {"basis", "temporal", "indicator_synthesis", "evidence_balance", "interpretation", "uncertainty", "restoration"}
    for problem_id, seed in seed_map.items():
        item = generated[problem_id]
        if set(item) != {"problem_id", "analysis_features", "analysis_context"}:
            issues.append({"code": "invalid_analysis_problem_fields", "message": problem_id})
            continue
        if not isinstance(item["analysis_features"], list) or not item["analysis_features"]:
            issues.append({"code": "invalid_analysis_features", "message": problem_id})
        elif not set(item["analysis_features"]).issubset(ALLOWED_ANALYSIS_FEATURES):
            issues.append({"code": "unknown_analysis_feature", "message": problem_id})
        context = item["analysis_context"]
        if not isinstance(context, dict) or set(context) != context_keys:
            issues.append({"code": "invalid_analysis_context", "message": problem_id})
            continue
        try:
            if _locked_context(context) != _locked_context(seed["analysis_context"]):
                issues.append({"code": "analysis_changed_locked_fact", "message": problem_id})
        except (KeyError, TypeError):
            issues.append({"code": "analysis_missing_locked_fact", "message": problem_id})
        for section, field in (
            ("basis", "basis_summary"), ("temporal", "summary"),
            ("indicator_synthesis", "synthesis"), ("evidence_balance", "summary"),
            ("restoration", "direction"),
        ):
            if not isinstance(context.get(section, {}).get(field), str) or not context[section][field].strip():
                issues.append({"code": "missing_analysis_explanation", "message": f"{problem_id}:{section}.{field}"})
        interpretation = context.get("interpretation", {})
        if not all(isinstance(interpretation.get(name), expected) for name, expected in (
            ("primary_dimensions", list), ("dimension_labels", list), ("primary_finding", str),
            ("secondary_findings", list), ("ecological_meaning", str),
        )):
            issues.append({"code": "invalid_interpretation", "message": problem_id})
        uncertainty = context.get("uncertainty", {})
        if not all(isinstance(uncertainty.get(name), expected) for name, expected in (
            ("data_limitations", list), ("interpretation_limitations", list),
            ("unsupported_inferences", list), ("confidence_reason", str),
        )):
            issues.append({"code": "invalid_uncertainty", "message": problem_id})
    cross = draft["cross_problem_analysis"]
    cross_keys = {"shared_signals", "contrasting_dimensions", "dimension_profiles", "dominant_ecological_dimension", "dominant_dimension_label", "integrated_interpretation", "uncertainty"}
    if not isinstance(cross, dict) or set(cross) != cross_keys:
        issues.append({"code": "invalid_cross_problem_analysis", "message": "required structure missing"})
    else:
        allowed_ids = set(seed_map)
        allowed_indicators = {name for item in seeds for name in item["supporting_indicators"]}
        for shared in cross["shared_signals"]:
            if not set(shared.get("related_problem_ids", [])).issubset(allowed_ids):
                issues.append({"code": "unknown_cross_problem_id", "message": str(shared)})
            if not set(shared.get("indicators", [])).issubset(allowed_indicators):
                issues.append({"code": "unknown_cross_indicator", "message": str(shared)})
        if cross["dimension_profiles"] != seed_cross["dimension_profiles"]:
            issues.append({"code": "cross_changed_dimension_profiles", "message": "dimension_profiles"})
        allowed_dimensions = {item["dimension"] for item in seed_cross["dimension_profiles"]}
        if cross["dominant_ecological_dimension"] not in allowed_dimensions | {None}:
            issues.append({"code": "unknown_dominant_dimension", "message": str(cross["dominant_ecological_dimension"])})
        if not isinstance(cross["integrated_interpretation"], str) or not cross["integrated_interpretation"].strip():
            issues.append({"code": "missing_cross_interpretation", "message": "integrated_interpretation"})
    return issues


def build_analysis_material(
    project: dict[str, Any],
    diagnoses: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    analysis_config: dict[str, Any],
    backend: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    seeds = build_analysis_contexts(diagnoses, facts, measures, analysis_config)
    seed_cross = build_cross_problem_analysis(seeds, analysis_config)
    if backend is None:
        return seeds, seed_cross, "deterministic_seed"
    draft = backend(build_analysis_payload(project, seeds, facts, rules, analysis_config, seed_cross))
    issues = validate_analysis_draft(draft, seeds, seed_cross)
    if issues:
        raise ValueError("Analysis Builder validation failed: " + json.dumps(issues, ensure_ascii=False))
    generated = {item["problem_id"]: item for item in draft["problems"]}
    enriched = []
    for seed in seeds:
        item = deepcopy(seed)
        item["analysis_features"] = generated[item["problem_id"]]["analysis_features"]
        item["analysis_context"] = generated[item["problem_id"]]["analysis_context"]
        enriched.append(item)
    return enriched, draft["cross_problem_analysis"], getattr(backend, "name", "custom")
