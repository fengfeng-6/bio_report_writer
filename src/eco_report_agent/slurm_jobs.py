from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{7,63}$")


@dataclass(frozen=True)
class ReportJobRequest:
    request_id: str
    project_type: str
    fid: int
    target_year: int
    project_name: str | None = None

    def validate(self) -> None:
        if not REQUEST_ID_PATTERN.fullmatch(self.request_id):
            raise ValueError("request_id must contain 8-64 safe characters")
        if self.project_type not in {"solar", "wind", "coal"}:
            raise ValueError("Unsupported project_type")
        if self.fid < 0:
            raise ValueError("fid must be non-negative")
        if not 1900 <= self.target_year <= 2200:
            raise ValueError("target_year is outside the supported range")
        if self.project_name and (
            len(self.project_name) > 200 or any(ord(char) < 32 for char in self.project_name)
        ):
            raise ValueError("project_name contains unsupported characters")


@dataclass(frozen=True)
class PreparedReportJob:
    request: ReportJobRequest
    directory: Path
    script: Path
    script_sha256: str


def render_sbatch_script(
    request: ReportJobRequest,
    *,
    project_root: Path,
    workbook_path: Path,
    job_directory: Path,
    partition: str = "debug",
) -> str:
    request.validate()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", partition):
        raise ValueError("partition contains unsupported characters")
    output_directory = job_directory / "result"
    command = [
        "python",
        "-m",
        "eco_report_agent.cli",
        "render-all",
        "--input",
        str(workbook_path),
        "--project",
        request.project_type,
        "--fid",
        str(request.fid),
        "--year",
        str(request.target_year),
        "--output-dir",
        str(output_directory),
    ]
    if request.project_name:
        command.extend(("--project-name", request.project_name))
    quoted_command = " ".join(shlex.quote(value) for value in command)
    return f"""#!/bin/bash
#SBATCH --partition={partition}
#SBATCH --job-name=eco_report_{request.request_id[:16]}
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --output={job_directory}/slurm-%j.out

set -euo pipefail

module load python/3.11.7-gcc-12.3.0
cd {shlex.quote(str(project_root))}
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src

{quoted_command}
"""


def prepare_report_job(
    request: ReportJobRequest,
    *,
    project_root: Path,
    workbook_path: Path,
    jobs_root: Path,
    partition: str = "debug",
) -> PreparedReportJob:
    request.validate()
    root = jobs_root.resolve()
    directory = (root / request.request_id).resolve()
    if directory.parent != root:
        raise ValueError("request_id escaped the jobs root")
    directory.mkdir(parents=True, exist_ok=False)
    script_text = render_sbatch_script(
        request,
        project_root=project_root.resolve(),
        workbook_path=workbook_path.resolve(),
        job_directory=directory,
        partition=partition,
    )
    script = directory / "run.sbatch"
    script.write_text(script_text, encoding="utf-8", newline="\n")
    (directory / "request.json").write_text(
        json.dumps(
            {
                "request_id": request.request_id,
                "project_type": request.project_type,
                "fid": request.fid,
                "target_year": request.target_year,
                "project_name": request.project_name,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    return PreparedReportJob(request, directory, script, digest)
