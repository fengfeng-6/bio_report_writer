from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from eco_report_agent.batch_runner import (
    _ordered_entries,
    _phase_plan,
    _pipeline_dependency_fingerprint,
    _should_enqueue,
    _slurm_job_end_epoch,
)
from eco_report_agent.checkpointing import (
    atomic_write_json,
    file_sha256,
    load_checkpoint,
    read_json,
    save_checkpoint,
    value_sha256,
)
from eco_report_agent.llm_http import (
    FatalLLMError,
    _retry_after_seconds,
    request_chat_json,
)
from eco_report_agent.mvp_writer import render_template_writer, validate_writer_output
from eco_report_agent.raw_batch import _set_snapshot_hashes, prepare_raw_batch
from eco_report_agent.snapshot_report import (
    _analysis_snapshot_hash,
    _chart_file_hashes,
    build_report_ir_from_snapshot,
    build_snapshot_observations,
)


def _snapshot(project_type: str = "wind", protected: bool = False) -> dict:
    main = []
    landscape = []
    for year in range(2017, 2025):
        main.extend(
            [
                {
                    "source_sheet": "风电",
                    "source_row": year,
                    "year": year,
                    "scope": "覆盖区",
                    "metrics": {
                        "NDVI": None,
                        "NPP": None,
                        "NEP": None,
                        "NCS": None,
                        "EVI": 0.2 + (year - 2017) * 0.01,
                        "SIF": 0.01,
                        "VCI": None,
                        "VCS": None,
                        "VCR": None,
                    },
                },
                {
                    "source_sheet": "风电",
                    "source_row": year + 100,
                    "year": year,
                    "scope": "0-1km",
                    "metrics": {
                        "NDVI": 0.8 - (year - 2017) * 0.05,
                        "NPP": 1.0 - (year - 2017) * 0.04,
                        "NEP": 2.0,
                        "NCS": 3.0,
                        "EVI": 0.3,
                        "SIF": 0.02,
                        "VCI": 40.0,
                        "VCS": 4.0,
                        "VCR": 0.9,
                    },
                },
            ]
        )
        landscape.extend(
            [
                {
                    "source_sheet": "风电景观破碎指数",
                    "source_row": year + 200,
                    "year": year,
                    "scope": "0-1km",
                    "category": "Total_Landscape",
                    "metrics": {
                        "CA": 100.0,
                        "LPI": 80.0 - (year - 2017),
                        "NP": 10.0 + (year - 2017),
                        "PD": 1.0 + (year - 2017) * 0.2,
                        "ED": 2.0 + (year - 2017) * 0.2,
                        "LSI": 3.0 + (year - 2017) * 0.2,
                    },
                },
                {
                    "source_sheet": "风电景观破碎指数",
                    "source_row": year + 300,
                    "year": year,
                    "scope": "0-1km",
                    "category": "cls_4",
                    "metrics": {
                        "CA": 50.0,
                        "LPI": 40.0,
                        "NP": 5.0,
                        "PD": 0.5,
                        "ED": 1.0,
                        "LSI": 2.0,
                    },
                },
            ]
        )
    protected_records = (
        [
            {
                "source_sheet": "保护区侵占情况",
                "source_row": 2,
                "scope": "0-1km",
                "protected_area_type": "KBA",
                "intersection_area_km2": 1.25,
            }
        ]
        if protected
        else []
    )
    value = {
        "format_version": 1,
        "project_key": f"{project_type}-0042",
        "project_type": project_type,
        "fid": 42,
        "target_year": 2024,
        "project_name": "风电项目 FID 42",
        "source_workbook_sha256": "a" * 64,
        "source_years": list(range(2017, 2025)),
        "main_records": main,
        "landscape_records": landscape,
        "protected_area_records": protected_records,
        "protected_area_status": "recorded" if protected else "not_recorded",
    }
    value["snapshot_sha256"] = value_sha256(value)
    return value


class SnapshotReportTests(unittest.TestCase):
    def test_wind_uses_per_indicator_core_fallback(self) -> None:
        observations, spatial = build_snapshot_observations(_snapshot())
        self.assertEqual(spatial["main_indicator_scope_selection"]["MEAN_NDVI"], "0-1km")
        self.assertEqual(spatial["main_indicator_scope_selection"]["MEAN_EVI"], "覆盖区")
        self.assertEqual(spatial["landscape_indicator_scope_selection"]["NP"], "0-1km")
        self.assertEqual(len(observations), 8)
        self.assertAlmostEqual(observations[-1].number("MEAN_NDVI"), 0.45)

    def test_report_ir_24_omits_risk_and_measures(self) -> None:
        report = build_report_ir_from_snapshot(_snapshot())
        self.assertEqual(report["schema_version"], "2.4-mvp")
        self.assertIsNone(report["overall_assessment"]["risk_level"])
        self.assertEqual(report["measure_recommendations"], [])
        self.assertEqual(
            report["spatial_evidence"]["landscape_class_semantics"], "mapped_cls_v1"
        )
        text = render_template_writer(report)
        self.assertNotIn("综合风险等级为None", text)
        self.assertIn("不生成总体风险等级", text)
        self.assertIn("不生成或补造具体修复措施", text)
        self.assertIn("按“未记录”处理", text)
        self.assertEqual(validate_writer_output(text, report), [])

    def test_protected_area_record_is_reported_without_time_trend(self) -> None:
        report = build_report_ir_from_snapshot(_snapshot(protected=True))
        text = render_template_writer(report)
        self.assertIn("KBA", text)
        self.assertIn("1.25平方公里", text)
        self.assertIn("不解释为时间趋势", text)


class CheckpointTests(unittest.TestCase):
    def test_retry_after_supports_seconds_and_http_date(self) -> None:
        self.assertEqual(_retry_after_seconds("15"), 15.0)
        self.assertIsInstance(
            _retry_after_seconds("Wed, 21 Oct 2037 07:28:00 GMT"), float
        )

    def test_project_name_change_preserves_analysis_hash(self) -> None:
        snapshot = {
            "project_key": "solar-0001",
            "project_name": "光伏项目 FID 1",
            "main_records": [{"year": 2024, "metrics": {"NDVI": 0.5}}],
        }
        _set_snapshot_hashes(snapshot)
        analysis_hash = snapshot["analysis_snapshot_sha256"]
        full_hash = snapshot["snapshot_sha256"]
        snapshot["project_name"] = "正式项目名称"
        _set_snapshot_hashes(snapshot)
        self.assertEqual(snapshot["analysis_snapshot_sha256"], analysis_hash)
        self.assertEqual(_analysis_snapshot_hash(snapshot), analysis_hash)
        self.assertNotEqual(snapshot["snapshot_sha256"], full_hash)

    def test_resume_applies_project_name_map_with_transient_requeue_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.xlsx"
            source.write_bytes(b"fixture")
            snapshot_root = root / "snapshots"
            snapshot_path = snapshot_root / "projects" / "solar" / "fid-0001.json"
            snapshot = {
                "format_version": 1,
                "project_key": "solar-0001",
                "project_type": "solar",
                "fid": 1,
                "target_year": 2024,
                "project_name": "光伏项目 FID 1",
            }
            _set_snapshot_hashes(snapshot)
            atomic_write_json(snapshot_path, snapshot)
            manifest = {
                "format_version": 1,
                "source_workbook_sha256": file_sha256(source),
                "target_year": 2024,
                "projects": [
                    {
                        "project_key": "solar-0001",
                        "project_name": snapshot["project_name"],
                        "snapshot_path": "projects/solar/fid-0001.json",
                        "snapshot_sha256": snapshot["snapshot_sha256"],
                    }
                ],
            }
            atomic_write_json(snapshot_root / "input_manifest.json", manifest)
            updated = prepare_raw_batch(
                source,
                snapshot_root,
                2024,
                resume=True,
                project_names={"solar-0001": "正式项目名称"},
            )
            self.assertTrue(updated["projects"][0]["_name_changed"])
            self.assertEqual(read_json(snapshot_path)["project_name"], "正式项目名称")
            persisted = read_json(snapshot_root / "input_manifest.json")
            self.assertNotIn("_name_changed", persisted["projects"][0])

    def test_chart_integrity_detects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chart_path = Path(directory) / "chart.png"
            chart_path.write_bytes(b"chart")
            charts = [{"path": str(chart_path)}]
            hashes = _chart_file_hashes(charts)
            self.assertIsNotNone(hashes)
            self.assertIn(str(chart_path), hashes)
            chart_path.unlink()
            self.assertIsNone(_chart_file_hashes(charts))

    def test_corrupted_checkpoint_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            save_checkpoint(
                path, stage="unit", fingerprint="fp", value={"answer": 42}
            )
            self.assertEqual(load_checkpoint(path, "fp"), {"answer": 42})
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["value"]["answer"] = 41
            atomic_write_json(path, payload)
            self.assertIsNone(load_checkpoint(path, "fp"))

    def test_http_retry_then_checkpoint_reuse(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "{\"ok\": true}"}}]}
                ).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TEST_ECO_KEY": "secret"}
        ), patch(
            "eco_report_agent.llm_http.urllib.request.urlopen",
            side_effect=[urllib.error.URLError("temporary"), Response()],
        ) as opened, patch("eco_report_agent.llm_http.time.sleep"):
            kwargs = {
                "base_url": "https://example.invalid/v1",
                "model": "test",
                "api_key_env": "TEST_ECO_KEY",
                "timeout_seconds": 1,
                "temperature": 0.0,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "test"}],
                "label": "unit",
                "checkpoint_directory": Path(directory),
                "max_attempts": 2,
            }
            result, attempts, _, cached = request_chat_json(**kwargs)
            self.assertEqual(result, {"ok": True})
            self.assertEqual(attempts, 2)
            self.assertFalse(cached)
            result, attempts, _, cached = request_chat_json(**kwargs)
            self.assertEqual(result, {"ok": True})
            self.assertEqual(attempts, 0)
            self.assertTrue(cached)
            self.assertEqual(opened.call_count, 2)

    def test_invalid_json_response_is_not_retried(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return b"not-json"

        with patch.dict(os.environ, {"TEST_ECO_KEY": "secret"}), patch(
            "eco_report_agent.llm_http.urllib.request.urlopen",
            return_value=Response(),
        ) as opened, patch("eco_report_agent.llm_http.time.sleep") as slept:
            with self.assertRaisesRegex(RuntimeError, "invalid LLM JSON response"):
                request_chat_json(
                    base_url="https://example.invalid/v1",
                    model="test",
                    api_key_env="TEST_ECO_KEY",
                    timeout_seconds=1,
                    temperature=0.0,
                    max_tokens=10,
                    messages=[],
                    label="invalid-json",
                    checkpoint_directory=None,
                )
        self.assertEqual(opened.call_count, 1)
        slept.assert_not_called()

    def test_response_is_cached_only_after_semantic_validation(self) -> None:
        class Response:
            def __init__(self, valid: bool):
                self.valid = valid

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                content = json.dumps({"valid": self.valid})
                return json.dumps(
                    {"choices": [{"message": {"content": content}}]}
                ).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TEST_ECO_KEY": "secret"}
        ), patch(
            "eco_report_agent.llm_http.urllib.request.urlopen",
            side_effect=[Response(False), Response(True)],
        ) as opened:
            kwargs = {
                "base_url": "https://example.invalid/v1",
                "model": "test",
                "api_key_env": "TEST_ECO_KEY",
                "timeout_seconds": 1,
                "temperature": 0.0,
                "max_tokens": 10,
                "messages": [],
                "label": "semantic",
                "checkpoint_directory": Path(directory),
                "cache_validator": lambda value: bool(value.get("valid")),
            }
            first = request_chat_json(**kwargs)
            second = request_chat_json(**kwargs)
            third = request_chat_json(**kwargs)
        self.assertFalse(first[0]["valid"])
        self.assertFalse(first[3])
        self.assertTrue(second[0]["valid"])
        self.assertFalse(second[3])
        self.assertTrue(third[3])
        self.assertEqual(opened.call_count, 2)

    def test_missing_key_is_global_fatal(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FatalLLMError):
                request_chat_json(
                    base_url="https://example.invalid/v1",
                    model="test",
                    api_key_env="MISSING_ECO_KEY",
                    timeout_seconds=1,
                    temperature=0,
                    max_tokens=10,
                    messages=[],
                    label="unit",
                    checkpoint_directory=None,
                )


class BatchPlanningTests(unittest.TestCase):
    def test_pipeline_dependency_fingerprint_changes_with_model(self) -> None:
        common = {
            "writer_base_url": "https://example.invalid/v1",
            "generated_date": "2026-08-31",
            "prepared_by": "test",
        }
        first = _pipeline_dependency_fingerprint(writer_model="model-a", **common)
        repeated = _pipeline_dependency_fingerprint(writer_model="model-a", **common)
        changed = _pipeline_dependency_fingerprint(writer_model="model-b", **common)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)

    def test_slurm_end_time_falls_back_to_squeue(self) -> None:
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "2026-08-31T23:16:58\n"},
        )()
        with patch.dict(os.environ, {"SLURM_JOB_ID": "61841241"}, clear=True), patch(
            "eco_report_agent.batch_runner.subprocess.run", return_value=completed
        ) as invoked:
            self.assertIsInstance(_slurm_job_end_epoch(), float)
        invoked.assert_called_once()

    def test_resume_requires_explicit_retry_failed_for_failures(self) -> None:
        self.assertFalse(_should_enqueue({"status": "succeeded"}, retry_failed=True))
        self.assertTrue(
            _should_enqueue(
                {"status": "succeeded"}, retry_failed=False, input_changed=True
            )
        )
        self.assertFalse(_should_enqueue({"status": "failed"}, retry_failed=False))
        self.assertTrue(_should_enqueue({"status": "failed"}, retry_failed=True))
        self.assertTrue(_should_enqueue({"status": "running"}, retry_failed=False))
        self.assertTrue(_should_enqueue({"status": "paused"}, retry_failed=False))

    def test_ramp_plan_respects_project_and_worker_limits(self) -> None:
        plan = _phase_plan(828, (5, 10, 20, 30))
        self.assertEqual(sum(size for size, _ in plan), 828)
        self.assertEqual(plan[:4], [(3, 3), (27, 5), (60, 10), (120, 20)])
        self.assertEqual(plan[-1], (618, 30))
        self.assertLessEqual(max(workers for _, workers in plan), 30)

    def test_stage_without_smoke_starts_at_requested_concurrency(self) -> None:
        self.assertEqual(_phase_plan(60, (10,), include_smoke=False), [(27, 10), (33, 10)])
        self.assertEqual(_phase_plan(120, (20,), include_smoke=False), [(27, 20), (60, 20), (33, 20)])

    def test_smoke_projects_are_scheduled_first(self) -> None:
        entries = [
            {"project_key": key, "project_type": key.split("-")[0], "fid": int(key.split("-")[1])}
            for key in ("coal-0033", "solar-0000", "wind-0042", "wind-0001")
        ]
        self.assertEqual(
            [item["project_key"] for item in _ordered_entries(entries)[:3]],
            ["solar-0000", "wind-0042", "coal-0033"],
        )


@unittest.skipUnless(
    os.environ.get("ECO_REPORT_RAW_SOURCE_XLSX"), "raw workbook not configured"
)
class PrivateRawWorkbookTests(unittest.TestCase):
    def test_real_workbook_enumerates_828_projects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = prepare_raw_batch(
                os.environ["ECO_REPORT_RAW_SOURCE_XLSX"], directory, 2024
            )
        self.assertEqual(manifest["project_count"], 828)
        self.assertEqual(
            manifest["project_counts"], {"coal": 95, "solar": 229, "wind": 504}
        )


if __name__ == "__main__":
    unittest.main()
