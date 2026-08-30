from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from eco_report_agent.cloud_release import build_cloud_release
from eco_report_agent.service import build_report_response
from eco_report_agent.smoke_fixture import SMOKE_FID, SMOKE_PROJECT, SMOKE_YEAR


class CloudReleaseTest(unittest.TestCase):
    project_root = Path(__file__).parents[1]

    def test_release_is_deterministic_and_excludes_private_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            result_one = build_cloud_release(self.project_root, first)
            result_two = build_cloud_release(self.project_root, second)
            self.assertEqual(result_one.sha256, result_two.sha256)
            self.assertEqual(first.read_bytes(), second.read_bytes())

            with tarfile.open(first, "r:gz") as archive:
                names = archive.getnames()
                self.assertFalse(any("private_inputs" in name for name in names))
                self.assertFalse(any(name.endswith(".docx") for name in names))
                self.assertEqual(
                    [name for name in names if name.endswith(".xlsx")],
                    ["eco-report-agent/fixtures/cloud-smoke.xlsx"],
                )
                manifest_file = archive.extractfile(
                    "eco-report-agent/release-manifest.json"
                )
                self.assertIsNotNone(manifest_file)
                manifest = json.loads(manifest_file.read())
                self.assertFalse(manifest["private_inputs_included"])
                self.assertEqual(manifest["cloud_mode"], "synthetic-smoke-test-only")
                for entry in manifest["files"]:
                    member = archive.extractfile("eco-report-agent/" + entry["path"])
                    self.assertIsNotNone(member)
                    self.assertEqual(
                        hashlib.sha256(member.read()).hexdigest(),
                        entry["sha256"],
                    )
                workbook_file = archive.extractfile(
                    "eco-report-agent/fixtures/cloud-smoke.xlsx"
                )
                self.assertIsNotNone(workbook_file)
                workbook = Path(directory) / "smoke.xlsx"
                workbook.write_bytes(workbook_file.read())
                report = build_report_response(
                    workbook,
                    {
                        "project_type": [SMOKE_PROJECT],
                        "fid": [str(SMOKE_FID)],
                        "target_year": [str(SMOKE_YEAR)],
                    },
                )
                self.assertEqual(report["fid"], SMOKE_FID)

    def test_compose_module_is_private_and_resource_limited(self) -> None:
        compose = (
            self.project_root / "deploy/cloud/docker-compose.eco-report.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("pull_policy: never", compose)
        self.assertIn("name: docker_default", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("mem_limit: 512m", compose)
        self.assertIn("cpus: 1.0", compose)
        self.assertIn("ECO_REPORT_ENABLE_PDF_RENDERER: \"0\"", compose)
        self.assertIn("fixtures/cloud-smoke.xlsx", compose)
        self.assertIn("eco-report-smoke-api", compose)
        self.assertNotIn("ports:", compose)
        self.assertNotIn("private_inputs", compose)


if __name__ == "__main__":
    unittest.main()
