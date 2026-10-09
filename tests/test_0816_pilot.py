from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))


class RiskEvidenceTest(unittest.TestCase):
    def test_trend_project_type_is_resolved_from_label(self) -> None:
        from eco_report_agent.raw_batch import _trend_project_type

        self.assertEqual(_trend_project_type("光伏项目 FID 227", 2), "solar")
        self.assertEqual(_trend_project_type("风电项目 FID 142", 3), "wind")
        with self.assertRaises(ValueError):
            _trend_project_type("项目 FID 6", 4)

    def test_annual_series_is_multi_scope_mean_and_invalid_p_is_missing(self) -> None:
        from eco_report_agent.snapshot_report import _build_risk_evidence

        snapshot = {
            "target_year": 2024,
            "project_type": "solar",
            "risk_records": [
                {"year": year, "scope": scope, "metrics": {"CERI": value, "LER": None, "ES": None, "EP": None, "RER": None, "CVR": None, "CERI_raw": None}, "risk_level": "中等", "risk_level_code": 3}
                for year, scope, value in ((2017, "本体", 1), (2017, "0-1km", 3), (2024, "本体", 5), (2024, "0-1km", 7))
            ],
            "risk_trend_record": {"p_value": None, "p_value_raw": 1.0985, "p_value_invalid": True, "trend_type": "增加"},
        }
        evidence = _build_risk_evidence(snapshot)
        self.assertEqual([item["mean_ceri"] for item in evidence["annual_series"]], [2.0, 6.0])
        self.assertEqual(evidence["trend"]["significance_status"], "missing")
        self.assertIn("invalid_p_value_treated_as_missing", evidence["quality_flags"])


class WriterRevisionPromptTest(unittest.TestCase):
    def test_counter_evidence_revision_contains_source_facts(self) -> None:
        from eco_report_agent.llm_writer import OpenAICompatibleNarrativeBackend

        diagnoses = []
        for problem_id, indicator in (("P001", "MEAN_NDVI"), ("P002", "MEAN_NPP"), ("P005", "ED")):
            diagnoses.append(
                {
                    "problem_id": problem_id,
                    "problem_name": f"问题{problem_id}",
                    "status": "突出问题",
                    "analysis_context": {
                        "indicator_synthesis": {
                            "conflicting_signals": [
                                {
                                    "evidence_id": f"EV-{problem_id}-{indicator}",
                                    "fact_ref": f"IF-{indicator}",
                                    "indicator": indicator,
                                    "long_term": "stable",
                                    "recent": "stable",
                                    "supports_problem": False,
                                    "turning_points": [],
                                }
                            ]
                        }
                    },
                }
            )
        report = {
            "problem_diagnoses": diagnoses,
            "indicator_facts": [
                {
                    "indicator": indicator,
                    "statistics": {"current_value": 1.0, "current_year": 2024},
                    "trend": {"relative_slope": 0.0},
                    "quality": {"valid_year_count": 8},
                }
                for indicator in ("MEAN_NDVI", "MEAN_NPP", "ED")
            ],
            "measure_recommendations": [],
        }
        backend = OpenAICompatibleNarrativeBackend.__new__(OpenAICompatibleNarrativeBackend)
        captured: dict[str, Any] = {}

        def capture(messages, label, cache_validator=None):
            captured["messages"] = messages
            return {"captured": True}

        backend._request = capture
        backend.revise(
            report,
            {"summary": {}, "sections": []},
            [
                {"code": "counter_evidence_omitted", "message": problem_id}
                for problem_id in ("P001", "P002", "P005")
            ],
        )
        correction = json.loads(captured["messages"][-1]["content"])
        requirements = {item["problem_id"]: item for item in correction["counter_evidence_requirements"]}
        self.assertEqual(set(requirements), {"P001", "P002", "P005"})
        for problem_id, indicator in (("P001", "MEAN_NDVI"), ("P002", "MEAN_NPP"), ("P005", "ED")):
            signal = requirements[problem_id]["required_counter_evidence"][0]
            self.assertEqual(signal["indicator"], indicator)
            self.assertEqual(signal["fact_ref"], f"IF-{indicator}")
            self.assertFalse(signal["supports_problem"])
            self.assertIn(problem_id, requirements[problem_id]["instruction"])

    def test_lexical_cleanup_revision_uses_compact_current_draft_only(self) -> None:
        from eco_report_agent.llm_writer import OpenAICompatibleNarrativeBackend

        backend = OpenAICompatibleNarrativeBackend.__new__(OpenAICompatibleNarrativeBackend)
        captured: dict[str, Any] = {}

        def capture(messages, label, cache_validator=None):
            captured["messages"] = messages
            return {"captured": True}

        backend._request = capture
        draft = {"summary": {"paragraphs": ["已验证事实"]}, "sections": []}
        backend.revise(
            {"problem_diagnoses": [{"problem_id": "P001"}], "indicator_facts": [{"indicator": "MEAN_NDVI"}], "measure_recommendations": []},
            draft,
            [
                {"code": "unsupported_inference_or_action", "message": "可能反映"},
                {"code": "machine_language", "message": "analysis_context"},
            ],
        )
        self.assertEqual(len(captured["messages"]), 2)
        request = json.loads(captured["messages"][-1]["content"])
        self.assertEqual(request["current_draft"], draft)
        self.assertEqual({item["code"] for item in request["issues"]}, {"unsupported_inference_or_action", "machine_language"})
        self.assertNotIn("problem_diagnoses", request)
        self.assertNotIn("build_writer_payload", json.dumps(captured["messages"], ensure_ascii=False))
        self.assertIn("只允许删除违规表达所在的完整句或从句", captured["messages"][0]["content"])
        self.assertIn("不得出现字面词", captured["messages"][0]["content"])

    def test_machine_language_revision_contains_hit_and_forbidden_terms(self) -> None:
        from eco_report_agent.llm_writer import OpenAICompatibleNarrativeBackend

        backend = OpenAICompatibleNarrativeBackend.__new__(OpenAICompatibleNarrativeBackend)
        captured: dict[str, Any] = {}

        def capture(messages, label, cache_validator=None):
            captured["messages"] = messages
            return {"captured": True}

        backend._request = capture
        draft = {"summary": {}, "sections": []}
        backend.revise(
            {"problem_diagnoses": [], "indicator_facts": [], "measure_recommendations": []},
            draft,
            [{"code": "machine_language", "message": "analysis_context"}],
        )
        request = json.loads(captured["messages"][-1]["content"])
        self.assertEqual(request["current_draft"], draft)
        self.assertEqual(request["issues"][0]["offending_fragment"], "analysis_context")
        self.assertIn("不得新增、改写或推断任何其他事实", captured["messages"][0]["content"])

    def test_length_revision_contains_source_diagnosis_and_target(self) -> None:
        from eco_report_agent.llm_writer import OpenAICompatibleNarrativeBackend

        report = {
            "problem_diagnoses": [
                {"problem_id": "P004", "problem_name": "突出问题", "status": "突出问题", "severity": "high", "trend": "worsening", "evidence_level": "高", "confidence": "high", "analysis_context": {"basis": {"basis_summary": "已验证事实"}}},
                {"problem_id": "P002", "problem_name": "观察问题", "status": "持续观察", "severity": "low", "trend": "stable", "evidence_level": "中", "confidence": "medium", "analysis_context": {"basis": {"basis_summary": "已验证事实"}}},
            ],
            "indicator_facts": [],
            "measure_recommendations": [],
        }
        backend = OpenAICompatibleNarrativeBackend.__new__(OpenAICompatibleNarrativeBackend)
        captured: dict[str, Any] = {}

        def capture(messages, label, cache_validator=None):
            captured["messages"] = messages
            return {"captured": True}

        backend._request = capture
        backend.revise(
            report,
            {"summary": {}, "sections": []},
            [
                {"code": "soft_length_range", "message": "P004:440, recommended 800-1500"},
                {"code": "problem_analysis_too_shallow", "message": "P002:70<80"},
            ],
        )
        correction = json.loads(captured["messages"][-1]["content"])
        requirements = {item["problem_id"]: item for item in correction["length_requirements"]}
        self.assertEqual(requirements["P004"]["target_length"], {"min": 1000, "max": 1300})
        self.assertEqual(requirements["P002"]["target_length"], {"min": 300, "max": 450})
        self.assertEqual(requirements["P004"]["source_diagnosis"]["problem_name"], "突出问题")
        self.assertIn("1000至1300", requirements["P004"]["instruction"])


class ExportShapeTest(unittest.TestCase):
    def test_dynamic_chart_builders_include_risk_chart(self) -> None:
        spec = importlib.util.spec_from_file_location("export_reports_postgres", ROOT / "scripts" / "export_reports_postgres.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        self.assertIn("figure-2-3", module.BUILDERS)
        report = {"report_metadata": {"current_year": 2024}, "risk_evidence": {"annual_series": [{"year": 2024, "mean_ceri": 4, "valid_scope_count": 2}], "spatial_gradient": [], "target_year": 2024}}
        self.assertEqual(module.risk(report)["annual_series"][0]["value"], 4.0)


if __name__ == "__main__":
    unittest.main()
