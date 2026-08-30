from __future__ import annotations

import argparse
import hashlib
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from eco_report_agent.slurm_jobs import ReportJobRequest, prepare_report_job, render_sbatch_script


def _request_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or submit a full report Slurm Job")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--project", required=True, choices=("solar", "wind", "coal"))
    parser.add_argument("--fid", required=True, type=int)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--project-name")
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--partition", default="debug")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--jobs-root", type=Path, default=Path("outputs/jobs"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    request = ReportJobRequest(
        request_id=args.request_id or _request_id(),
        project_type=args.project,
        fid=args.fid,
        target_year=args.year,
        project_name=args.project_name,
    )
    preview_directory = args.jobs_root.resolve() / request.request_id
    script_text = render_sbatch_script(
        request,
        project_root=args.project_root.resolve(),
        workbook_path=args.input.resolve(),
        job_directory=preview_directory,
        partition=args.partition,
    )
    digest = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
    if not args.apply:
        print(
            f"dry_run=true request_id={request.request_id} partition={args.partition} "
            f"script_sha256={digest}"
        )
        return 0

    prepared = prepare_report_job(
        request,
        project_root=args.project_root,
        workbook_path=args.input,
        jobs_root=args.jobs_root,
        partition=args.partition,
    )
    completed = subprocess.run(
        ["sbatch", "--parsable", str(prepared.script)],
        check=True,
        capture_output=True,
        text=True,
    )
    slurm_job_id = completed.stdout.strip().split(";", 1)[0]
    print(
        f"submitted=true request_id={request.request_id} slurm_job_id={slurm_job_id} "
        f"script_sha256={prepared.script_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
