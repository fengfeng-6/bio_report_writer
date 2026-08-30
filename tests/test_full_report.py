from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eco_report_agent.full_report import build_full_report_bundle
from eco_report_agent.smoke_fixture import SMOKE_FID, SMOKE_PROJECT, SMOKE_YEAR, build_smoke_workbook


class FullReportTest(unittest.TestCase):
    def test_builds_validated_hpc_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "smoke.xlsx"
            workbook.write_bytes(build_smoke_workbook())
            bundle = build_full_report_bundle(
                workbook,
                SMOKE_PROJECT,
                SMOKE_FID,
                SMOKE_YEAR,
                "合成测试项目",
                root / "result",
            )
            manifest = json.loads(bundle.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["execution_location"], "hpc-slurm")
            self.assertTrue(manifest["validation"]["valid"])
            self.assertEqual(set(manifest["files"]), {"report.md", "report.docx", "report.pdf"})
            self.assertTrue(bundle.docx.read_bytes().startswith(b"PK"))
            self.assertTrue(bundle.pdf.read_bytes().startswith(b"%PDF"))

    def test_refuses_to_overwrite_existing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                build_full_report_bundle(
                    Path(directory) / "unused.xlsx",
                    SMOKE_PROJECT,
                    SMOKE_FID,
                    SMOKE_YEAR,
                    None,
                    Path(directory),
                )
