from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenAICompatibleWriterConfig:
    base_url: str
    model: str
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY"
    timeout_seconds: int = 240
    temperature: float = 0.25
    max_tokens: int = 16000


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
    return payload


SYSTEM_PROMPT = """你是正式生态报告 Writer，不负责生态诊断。
输入是一份已经校验的、仅包含可报告问题的语义材料。所有指标事实、诊断状态、趋势、证据关系、措施和推理边界均已确定。
请输出一个 JSON ReportDraft，顶层只包含 summary 和 sections。
summary={paragraphs:[字符串],keywords:[3至6个短语]}。
sections 必须恰好包含 overall_assessment、problem_analysis、cross_problem、restoration、conclusion 五种 section_id，每项包含 section_id、title、paragraphs；problem_analysis 额外包含 problem_blocks。
problem_blocks 必须覆盖输入中的全部问题且每个只出现一次；每项包含 problem_id、title、paragraphs。先写“突出问题”，再写“需要持续观察的潜在问题”；同组内按问题本身形成自然叙事，不解释排序依据。
不要逐字段机械输出，不要让不同问题采用同一段落结构。应根据已有分析素材自主选择长期/近期关系、多指标支持或冲突、生态含义、不确定性和修复含义中最有解释价值的内容，并跨段去重。
突出问题建议800至1500字，持续观察问题建议200至500字；这是软约束，分析密度优先。突出问题必须解释诊断如何由证据形成、保留反向证据、说明边界及修复含义。
如果 conflicting_signals 非空，正文必须逐一明确点名其中的反向指标，并用“但、然而、不一致、未支持”等转折说明其为何限制结论强度。
不得改变任何数值、趋势、诊断、confidence 和证据关系；不得新增问题、措施、现场事实、因果关系或工程参数；不得删除重要反向证据。
正文必须像专业技术人员撰写的正式报告，不得提及 ReportIR、Agent、Writer、JSON、分析框架、字段名、规则编号、原始条件名、True/False、机器标签、提示词或生成过程。禁止出现“诊断规则要求”“关键条件未满足”“当前结论不可评估”“归入P0级”等系统判定话术，也不得出现任何优先级、评分或P0至P3表述。
输入未包含数据不足或不适用问题；不得推测、补写或提及这些问题。
禁止用“可能反映、通常反映、措施生效、自然波动、局部环境因子、饱和点、污染压力、驱动机制、风险源解析、敏感性评估、排查原因/因子”等表述补造原因，也禁止新增监测、监控、干预、投入或调查行动。可以明确说明原因未知或证据不足，但不能提出原因假设。
修复部分只能复述 measure_recommendations 中已经合规匹配的措施及其既有边界；某问题没有匹配措施时，只能说明尚无合规匹配，不得自行提出行动。
只返回 JSON 对象，不要返回 Markdown。"""


class OpenAICompatibleNarrativeBackend:
    name = "llm"

    def __init__(self, config: OpenAICompatibleWriterConfig):
        self.config = config
        self.request_count = 0
        self.request_durations_seconds: list[float] = []

    def _request(self, messages: list[dict[str, str]], label: str) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"missing API key environment variable: {self.config.api_key_env}")
        endpoint = self.config.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        print(f"[writer] requesting {label}", flush=True)
        self.request_count += 1
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"writer API HTTP {exc.code}: {detail}") from exc
        finally:
            self.request_durations_seconds.append(round(time.perf_counter() - started, 3))
        content = payload["choices"][0]["message"]["content"]
        result = content if isinstance(content, dict) else json.loads(content)
        print(f"[writer] completed {label}", flush=True)
        return result

    def __call__(self, report: dict[str, Any], _baseline: str) -> dict[str, Any]:
        return self._request(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(build_writer_payload(report), ensure_ascii=False)},
            ],
            "ReportDraft",
        )

    def revise(self, report: dict[str, Any], draft: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
        correction = {
            "task": "修正上一版 ReportDraft",
            "validation_issues": issues,
            "requirements": [
                "只修正列出的问题，不改变输入中的任何事实、诊断、证据关系或措施边界",
                "不得通过新增原因假设、现场事实、措施、调查、监测或工程参数来扩写",
                "若问题是篇幅不足，只能深化既有证据之间的关系、反向证据、时间尺度和不确定性",
                "清除规则编号、字段名、系统判定话术以及优先级或P0至P3表述",
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
        )
