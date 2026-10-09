from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from eco_report_agent.models import DataScope, Observation, ProjectType
from eco_report_agent.mvp import (
    build_indicator_fact,
    build_measure_recommendations,
    build_problem_diagnoses,
    build_report_ir_mvp,
    load_json_config,
    validate_report_ir_mvp,
)
from eco_report_agent.analysis_builder import OpenAICompatibleAnalysisBackend, build_analysis_payload, merge_cross_analysis, validate_analysis_draft
from eco_report_agent.llm_writer import SYSTEM_PROMPT, build_writer_payload
from eco_report_agent.mvp_charts import build_report_charts
from eco_report_agent.mvp_writer import render_template_writer, validate_narrative_draft, validate_writer_output, write_report

from .test_pipeline import _row
from .xlsx_fixture import make_workbook


def _observations(values: list[float]) -> list[Observation]:
    return [Observation(ProjectType.COAL, 33, 2017 + index, {"MEAN_NDVI": value}, index + 2) for index, value in enumerate(values)]


def _report_draft(report: dict, machine_language: bool = False) -> dict:
    blocks = []
    reportable = [item for item in report["problem_diagnoses"] if item["status"] not in {"数据不足", "不适用"}]
    reportable.sort(key=lambda item: (0 if item["status"] == "突出问题" else 1, item["problem_id"]))
    for index, item in enumerate(reportable):
        context = item["analysis_context"]
        indicators = "、".join(item["supporting_indicators"])
        text = f"{item['problem_name']}依据{indicators}形成当前判断。长期与近期变化需要综合理解，但现有反向证据不能忽略，结论不得超出现有数据边界。"
        conflicting = [signal["indicator"] for signal in context["indicator_synthesis"].get("conflicting_signals", [])]
        if conflicting:
            text += f"但{'、'.join(conflicting)}未支持同一变化方向，因此限制了结论强度。"
        if machine_language and index == 0:
            text += "诊断规则PR-P012要求的关键条件historical_abandoned_mine未满足，当前结论不可评估。"
        length = 420 if item["status"] == "突出问题" else 120
        text += "现有证据用于解释诊断而不推断现场原因。" * max(1, length // 20)
        blocks.append({"problem_id": item["problem_id"], "title": item["problem_name"], "paragraphs": [text]})
    return {
        "summary": {"paragraphs": ["基于结构化指标事实形成生态诊断，所有结论均受证据边界约束。"], "keywords": ["生态诊断", "指标趋势", "生态修复"]},
        "sections": [
            {"section_id": "overall_assessment", "title": "综合生态状况", "paragraphs": ["综合态势由各生态维度共同构成。"]},
            {"section_id": "problem_analysis", "title": "生态问题分析", "paragraphs": ["问题依据现有证据分别展开。"], "problem_blocks": blocks},
            {"section_id": "cross_problem", "title": "主要生态矛盾", "paragraphs": ["跨问题综合保留维度差异。"]},
            {"section_id": "restoration", "title": "修复方向", "paragraphs": ["仅采用已匹配措施，不补充工程参数。"]},
            {"section_id": "conclusion", "title": "结论", "paragraphs": ["结论不超出现有指标与证据范围。"]},
        ],
    }


class IndicatorFactMvpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_json_config("trend_config.json")

    def test_expected_trend_patterns(self) -> None:
        cases = [
            ([1, 2, 3, 4, 5], "overall_up"),
            ([5, 4, 3, 2, 1], "overall_down"),
            ([10, 10.1, 9.9, 10], "stable"),
            ([1, 3, 2, 4, 3, 6], "fluctuating_upward"),
            ([6, 4, 5, 3, 4, 1], "fluctuating_downward"),
            ([1, 2, 4, 3, 1], "rise_then_fall"),
            ([4, 3, 1, 2, 5], "fall_then_rise"),
        ]
        for values, expected in cases:
            with self.subTest(expected=expected):
                fact = build_indicator_fact(_observations(values), "MEAN_NDVI", self.config)
                self.assertEqual(fact["trend"]["long_term"], expected)

    def test_less_than_three_years_is_unknown(self) -> None:
        fact = build_indicator_fact(_observations([1, 2]), "MEAN_NDVI", self.config)
        self.assertFalse(fact["valid"])
        self.assertEqual(fact["trend"]["long_term"], "unknown")


class MvpIntegrationTest(unittest.TestCase):
    def test_shengli_contract_scenarios(self) -> None:
        facts = [
            {"indicator": "MEAN_NDVI", "valid": True, "trend": {"long_term": "overall_up", "recent": "up"}},
            {"indicator": "MEAN_NPP", "valid": True, "trend": {"long_term": "overall_up", "recent": "up"}},
            {"indicator": "NP", "valid": True, "trend": {"long_term": "overall_up", "recent": "down"}},
            {"indicator": "PD", "valid": True, "trend": {"long_term": "overall_up", "recent": "down"}},
        ]
        diagnoses = build_problem_diagnoses("coal", ["植被退化"], facts, DataScope())
        by_id = {item["problem_id"]: item for item in diagnoses}
        self.assertEqual(by_id["P001"]["status"], "存在但改善中")
        self.assertEqual(by_id["P004"]["status"], "突出问题")
        self.assertEqual(by_id["P004"]["trend"], "recently_improving")
        self.assertEqual(by_id["P003"]["status"], "数据不足")
        self.assertEqual(by_id["P011"]["status"], "不适用")

    def test_report_ir_and_writer_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(
                Path(directory) / "fixture.xlsx",
                [
                    _row(2023, 0.2, "植被退化", "乡土种子库人工补播；设置光伏板"),
                    _row(2024, 0.3, "植被退化", "乡土种子库人工补播；设置光伏板"),
                    _row(2025, 0.4, "植被退化", "乡土种子库人工补播；设置光伏板"),
                ],
            )
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        self.assertEqual(report["schema_version"], "2.3-mvp")
        self.assertNotIn("priority_assessment", report)
        self.assertFalse(report["audit"]["priority_module_enabled"])
        self.assertFalse(validate_report_ir_mvp(report))
        self.assertIn("cross_problem_analysis", report)
        self.assertTrue(all(item["analysis_features"] for item in report["problem_diagnoses"]))
        self.assertTrue(all("analysis_context" in item for item in report["problem_diagnoses"]))
        self.assertTrue(all("diagnosis" in item for item in report["problem_diagnoses"]))
        self.assertEqual(report["audit"]["analysis_backend"], "deterministic_seed")
        self.assertEqual(report["audit"]["analysis_request_count"], 0)
        self.assertEqual(report["audit"]["analysis_request_durations_seconds"], [])
        self.assertEqual(report["audit"]["analysis_request_seconds"], 0)
        markdown = render_template_writer(report)
        self.assertFalse(validate_writer_output(markdown, report))
        self.assertNotIn("设置光伏板", markdown)
        self.assertNotIn("### 3.11 光伏治沙双向效应", markdown)
        self.assertNotIn("数据不足与不适用问题", markdown)
        self.assertNotIn("优先级", markdown)
        self.assertIn("<!-- toc -->", markdown)
        self.assertNotIn("支持状态为True", markdown)
        for internal_value in ("recently_improving", "not_assessable", "workbook_measure_text", "sufficient", "ca_landcover_type", "has_buffer_gradient", "historical_abandoned_mine"):
            self.assertNotIn(internal_value, markdown)
        omitted_names = [item["problem_name"] for item in report["problem_diagnoses"] if item["status"] in {"数据不足", "不适用"}]
        self.assertTrue(all(name not in markdown for name in omitted_names))

    def test_measure_can_trace_to_multiple_problems(self) -> None:
        diagnoses = [
            {"problem_id": "P005", "status": "需要持续观察"},
            {"problem_id": "P004", "status": "突出问题"},
            {"problem_id": "P011", "status": "不适用"},
        ]
        measures, _ = build_measure_recommendations(
            "矿区外围生态防护林带搭建",
            "coal",
            DataScope(),
            diagnoses,
        )
        self.assertEqual(len(measures), 1)
        self.assertEqual(measures[0]["target_problem_ids"], ["P005", "P004"])
        self.assertEqual(measures[0]["measure_id"], "M004")
        self.assertNotIn("priority", measures[0])

    def test_writer_payload_excludes_omitted_problems_and_internal_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        payload = build_writer_payload(report)
        self.assertTrue(all(item["status"] not in {"数据不足", "不适用"} for item in payload["problem_diagnoses"]))
        self.assertNotIn("priority_assessment", payload)
        self.assertNotIn("audit", payload)
        self.assertNotIn("report_assets", payload)
        self.assertNotIn("project_attributes", payload["project"])
        self.assertTrue(all("rule_id" not in item.get("analysis_context", {}).get("basis", {}) for item in payload["problem_diagnoses"]))

    def test_writer_payload_sanitizes_unsupported_source_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        report["problem_diagnoses"][0]["analysis_context"]["basis"]["basis_summary"] = "问题规则指定的核心指标，规则ID为PR-P001。"
        report["problem_diagnoses"][0]["analysis_context"]["uncertainty"]["confidence_reason"] = "关键条件不足，当前结论不可评估。"
        report["problem_diagnoses"][0]["analysis_context"]["uncertainty"]["interpretation_limitations"] = [
            "该结果可能反映数据分辨率或模型限制，而非真实生态稳定性。",
            "建议在后续监测中排查原因。",
        ]
        payload = build_writer_payload(report)
        serialized = str(payload)
        self.assertNotIn("可能反映", serialized)
        self.assertNotIn("规则指定", serialized)
        self.assertNotIn("PR-P001", serialized)
        self.assertNotIn("ca_landcover_type", serialized)
        self.assertNotIn("当前结论不可评估", serialized)
        self.assertIn("现有数据不足以判断具体原因", serialized)
        self.assertIn("现有数据不足以判断具体原因", SYSTEM_PROMPT)
        self.assertIn("字面词“可能”或“建议”", SYSTEM_PROMPT)

    def test_charts_export_scientific_raster_and_vector_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
            assets = build_report_charts(report, Path(directory) / "charts")
            self.assertEqual(len(assets), 3)
            for asset in assets:
                self.assertTrue(Path(asset["path"]).is_file())
                self.assertTrue(Path(asset["vector_path"]).is_file())
                self.assertTrue(Path(asset["pdf_path"]).is_file())
                self.assertEqual(asset["dpi"], 300)
            matrix_svg = Path(assets[-1]["vector_path"]).read_text(encoding="utf-8")
            landscape_svg = Path(assets[1]["vector_path"]).read_text(encoding="utf-8")
            self.assertIn("NP 与 PD 相对变化曲线重合", landscape_svg)
            self.assertNotIn("优先级", matrix_svg)
            omitted_names = [item["problem_name"] for item in report["problem_diagnoses"] if item["status"] in {"数据不足", "不适用"}]
            self.assertTrue(all(name not in matrix_svg for name in omitted_names))

    def test_narrative_rejects_machine_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        issues = validate_narrative_draft(_report_draft(report, machine_language=True), report)
        self.assertIn("machine_language", {item["code"] for item in issues})

    def test_narrative_rejects_unsupported_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        draft["summary"]["paragraphs"][0] += "该变化可能处于自然恢复后的稳定阶段。"
        issues = validate_narrative_draft(draft, report)
        self.assertIn("unsupported_inference_or_action", {item["code"] for item in issues})

    def test_analysis_builder_rejects_changed_locked_fact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = {
            "problems": [
                {"problem_id": item["problem_id"], "analysis_features": item["analysis_features"], "analysis_context": deepcopy(item["analysis_context"])}
                for item in report["problem_diagnoses"]
            ],
            "cross_problem_analysis": deepcopy(report["cross_problem_analysis"]),
        }
        draft["problems"][0]["analysis_context"]["basis"]["rule_id"] = "changed-rule"
        issues = validate_analysis_draft(draft, report["problem_diagnoses"], report["cross_problem_analysis"])
        self.assertIn("analysis_changed_locked_fact", {item["code"] for item in issues})

    def test_llm_analysis_backend_splits_by_diagnosable_problem(self) -> None:
        class FakeBackend(OpenAICompatibleAnalysisBackend):
            def __init__(self) -> None:
                self.calls = []

            def _request(self, payload, prompt, max_tokens=None):
                self.calls.append(payload)
                if "problem" in payload:
                    problem = payload["problem"]
                    context = problem["analysis_context"]
                    generated_context = {
                        "basis": {"basis_summary": context["basis"]["basis_summary"]},
                        "temporal": {"summary": context["temporal"]["summary"]},
                        "indicator_synthesis": {"synthesis": context["indicator_synthesis"]["synthesis"]},
                        "evidence_balance": {"summary": context["evidence_balance"]["summary"]},
                        "interpretation": {name: context["interpretation"][name] for name in ("primary_finding", "secondary_findings", "ecological_meaning")},
                        "uncertainty": {name: context["uncertainty"][name] for name in ("interpretation_limitations", "confidence_reason")},
                        "restoration": {"direction": context["restoration"]["direction"]},
                    }
                    return {"problem": {"problem_id": problem["problem_id"], "analysis_features": problem["analysis_features"], "analysis_context": generated_context}}
                cross = payload["cross_problem_analysis"]
                return {"cross_problem_analysis": {name: deepcopy(cross[name]) for name in ("shared_signals", "contrasting_dimensions", "dominant_ecological_dimension", "integrated_interpretation")}}

        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        backend = FakeBackend()
        payload = build_analysis_payload(
            report["project"], report["problem_diagnoses"], report["indicator_facts"],
            load_json_config("problem_rules.json"), load_json_config("analysis_config.json"), report["cross_problem_analysis"],
        )
        draft = backend(payload)
        expected_problem_calls = sum(item["applicability"] == "applicable" and item["data_sufficiency"] == "sufficient" for item in report["problem_diagnoses"])
        self.assertEqual(len(backend.calls), expected_problem_calls + 1)
        self.assertEqual(backend.fallback_problem_ids, [])
        self.assertFalse(backend.cross_fallback)
        self.assertFalse(validate_analysis_draft(draft, report["problem_diagnoses"], report["cross_problem_analysis"]))

    def test_llm_analysis_boundary_falls_back_per_problem(self) -> None:
        class BoundaryBackend(OpenAICompatibleAnalysisBackend):
            def __init__(self) -> None:
                pass

            def _request(self, payload, prompt, max_tokens=None):
                if "problem" in payload:
                    problem = payload["problem"]
                    context = problem["analysis_context"]
                    generated_context = {
                        "basis": {"basis_summary": context["basis"]["basis_summary"]},
                        "temporal": {"summary": context["temporal"]["summary"]},
                        "indicator_synthesis": {"synthesis": context["indicator_synthesis"]["synthesis"]},
                        "evidence_balance": {"summary": context["evidence_balance"]["summary"]},
                        "interpretation": {
                            "primary_finding": "该变化可能反映未知原因。" if problem["problem_id"] == "P001" else context["interpretation"]["primary_finding"],
                            "secondary_findings": context["interpretation"]["secondary_findings"],
                            "ecological_meaning": context["interpretation"]["ecological_meaning"],
                        },
                        "uncertainty": {name: context["uncertainty"][name] for name in ("interpretation_limitations", "confidence_reason")},
                    }
                    return {"problem": {"problem_id": problem["problem_id"], "analysis_features": problem["analysis_features"], "analysis_context": generated_context}}
                cross = payload["cross_problem_analysis"]
                return {"cross_problem_analysis": {name: deepcopy(cross[name]) for name in ("shared_signals", "contrasting_dimensions", "dominant_ecological_dimension", "integrated_interpretation")}}

        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        backend = BoundaryBackend()
        payload = build_analysis_payload(
            report["project"], report["problem_diagnoses"], report["indicator_facts"],
            load_json_config("problem_rules.json"), load_json_config("analysis_config.json"), report["cross_problem_analysis"],
        )
        draft = backend(payload)
        generated = {item["problem_id"]: item for item in draft["problems"]}
        seed = {item["problem_id"]: item for item in report["problem_diagnoses"]}
        self.assertEqual(backend.fallback_problem_ids, ["P001"])
        self.assertEqual(generated["P001"]["analysis_context"], seed["P001"]["analysis_context"])
        self.assertFalse(validate_analysis_draft(draft, report["problem_diagnoses"], report["cross_problem_analysis"]))

    def test_cross_analysis_rejects_unsupported_state_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        generated = {name: deepcopy(report["cross_problem_analysis"][name]) for name in ("shared_signals", "contrasting_dimensions", "dominant_ecological_dimension", "integrated_interpretation")}
        generated["integrated_interpretation"] += "项目可能处于自然恢复阶段。"
        with self.assertRaisesRegex(ValueError, "unsupported cross-problem"):
            merge_cross_analysis(report["cross_problem_analysis"], generated)

    def test_report_draft_covers_only_reportable_problems_and_controls_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        self.assertFalse(validate_narrative_draft(draft, report))
        result = write_report(report, lambda _report, _baseline: draft)
        self.assertEqual(result.backend, "llm")
        self.assertFalse(result.boundary_repaired)
        self.assertIsNotNone(result.draft)
        self.assertIn("持续观察的潜在问题", result.markdown)
        omitted_names = [item["problem_name"] for item in report["problem_diagnoses"] if item["status"] in {"数据不足", "不适用"}]
        self.assertTrue(all(name not in result.markdown for name in omitted_names))

    def test_writer_revises_invalid_draft_once(self) -> None:
        class RevisingBackend:
            def __init__(self, clean_draft):
                self.clean_draft = clean_draft
                self.revision_calls = 0

            def __call__(self, _report, _baseline):
                invalid = deepcopy(self.clean_draft)
                invalid["summary"]["paragraphs"][0] += "该变化可能反映局部环境因子。"
                return invalid

            def revise(self, _report, _draft, issues):
                self.revision_calls += 1
                self.asserted_issue_codes = {item["code"] for item in issues}
                return deepcopy(self.clean_draft)

        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        backend = RevisingBackend(_report_draft(report))
        result = write_report(report, backend)
        self.assertEqual(result.backend, "llm")
        self.assertFalse(result.boundary_repaired)
        self.assertEqual(backend.revision_calls, 1)
        self.assertIn("unsupported_inference_or_action", backend.asserted_issue_codes)

    def test_writer_keeps_valid_draft_when_soft_revision_times_out(self) -> None:
        class SlowRevisionBackend:
            def __init__(self, draft):
                self.draft = draft

            def __call__(self, _report, _baseline):
                return deepcopy(self.draft)

            def revise(self, _report, _draft, _issues):
                raise TimeoutError("simulated")

        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        result = write_report(report, SlowRevisionBackend(draft))
        self.assertEqual(result.backend, "llm")
        self.assertIsNotNone(result.draft)

    def test_writer_rejects_forbidden_sentence_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        draft["summary"]["paragraphs"][0] += "该变化可能反映局部环境因子。"
        result = write_report(report, lambda _report, _baseline: draft)
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))
        self.assertFalse(result.boundary_repaired)
        self.assertNotIn("可能反映", result.markdown)

    def test_machine_language_issue_keeps_safe_hit_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        draft["summary"]["paragraphs"][0] += "analysis_context"
        issues = validate_narrative_draft(draft, report)
        machine = next(issue for issue in issues if issue["code"] == "machine_language")
        self.assertEqual(machine["message"], "analysis_context")

    def test_writer_rejects_shallow_problem_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        problem_section = next(section for section in draft["sections"] if section["section_id"] == "problem_analysis")
        problem_section["problem_blocks"][0]["paragraphs"] = ["现有证据支持当前判断。诊断规则要求关键条件满足。该变化通常反映自然波动。"]
        result = write_report(report, lambda _report, _baseline: draft)
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))
        self.assertFalse(result.boundary_repaired)
        self.assertNotIn("自然波动", result.markdown)
        self.assertNotIn("诊断规则", result.markdown)

    def test_writer_rejects_missing_counter_evidence_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        conflicts = {
            item["problem_id"]
            for item in report["problem_diagnoses"]
            if item["status"] not in {"数据不足", "不适用"}
            and item["analysis_context"]["indicator_synthesis"]["conflicting_signals"]
        }
        problem_section = next(section for section in draft["sections"] if section["section_id"] == "problem_analysis")
        block = next(block for block in problem_section["problem_blocks"] if block["problem_id"] in conflicts)
        block["paragraphs"] = ["现有证据形成当前判断，结论严格限定在已有指标范围内。" * 20]
        result = write_report(report, lambda _report, _baseline: draft)
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))
        self.assertFalse(result.boundary_repaired)

    def test_writer_all_failed_revisions_do_not_repair_into_success(self) -> None:
        class AlwaysInvalidBackend:
            def __init__(self, draft):
                self.draft = draft
                self.revision_calls = 0

            def __call__(self, _report, _baseline):
                return deepcopy(self.draft)

            def revise(self, _report, _draft, _issues):
                self.revision_calls += 1
                return deepcopy(self.draft)

        with tempfile.TemporaryDirectory() as directory:
            workbook = make_workbook(Path(directory) / "fixture.xlsx", [_row(year, 0.2 + index * 0.1, "植被退化", "") for index, year in enumerate((2023, 2024, 2025))])
            report = build_report_ir_mvp(workbook, "coal", 33, 2025, "测试项目")
        draft = _report_draft(report)
        draft["summary"]["paragraphs"][0] += "该变化可能反映局部环境因子。"
        backend = AlwaysInvalidBackend(draft)
        result = write_report(report, backend)
        self.assertEqual(backend.revision_calls, 3)
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))
        self.assertFalse(result.boundary_repaired)

    def test_llm_invalid_output_falls_back(self) -> None:
        report = {
            "report_metadata": {"current_year": 2025, "evaluation_start_year": 2023, "evaluation_end_year": 2025},
            "project": {"name": "测试", "fid": 1, "project_type": "coal"},
            "overall_assessment": {"risk_level": "低"},
            "problem_diagnoses": [], "indicator_facts": [],
            "data_profile": {"available_indicators": [], "missing_indicators": []},
            "measure_recommendations": [], "audit": {"measure_filter": []},
        }
        result = write_report(report, lambda _report, _baseline: "无效输出")
        self.assertEqual(result.backend, "template")
        self.assertTrue(result.fallback_reason.startswith("llm_validation_failed"))


if __name__ == "__main__":
    unittest.main()
