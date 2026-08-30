from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eco_report_agent.slurm_jobs import ReportJobRequest, prepare_report_job, render_sbatch_script


class SlurmJobsTest(unittest.TestCase):
    def test_renders_fixed_resource_report_job(self) -> None:
        request = ReportJobRequest("request-1234", "coal", 33, 2025, "胜利矿")
        script = render_sbatch_script(
            request,
            project_root=Path("/project"),
            workbook_path=Path("/private/input.xlsx"),
            job_directory=Path("/project/outputs/jobs/request-1234"),
        )
        self.assertIn("#SBATCH --partition=debug", script)
        self.assertIn("#SBATCH --cpus-per-task=2", script)
        self.assertIn("#SBATCH --mem=4G", script)
        self.assertIn("render-all", script)
        self.assertIn("--project-name", script)

    def test_prepares_job_in_unique_audited_directory(self) -> None:
        request = ReportJobRequest("request-5678", "coal", 33, 2025)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_report_job(
                request,
                project_root=root / "project",
                workbook_path=root / "input.xlsx",
                jobs_root=root / "jobs",
            )
            self.assertTrue(prepared.script.is_file())
            self.assertTrue((prepared.directory / "request.json").is_file())
            with self.assertRaises(FileExistsError):
                prepare_report_job(
                    request,
                    project_root=root / "project",
                    workbook_path=root / "input.xlsx",
                    jobs_root=root / "jobs",
                )

    def test_rejects_unsafe_request_id(self) -> None:
        request = ReportJobRequest("../escape", "coal", 33, 2025)
        with self.assertRaises(ValueError):
            request.validate()


if __name__ == "__main__":
    unittest.main()
