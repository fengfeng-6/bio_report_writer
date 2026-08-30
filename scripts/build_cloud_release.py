from __future__ import annotations

import argparse
from pathlib import Path

from eco_report_agent.cloud_release import build_cloud_release


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic cloud deployment bundle")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/eco-report-agent-cloud.tar.gz"),
    )
    args = parser.parse_args()
    release = build_cloud_release(args.project_root, args.output)
    print(
        f"release_path={release.path} sha256={release.sha256} "
        f"file_count={release.file_count} private_inputs_included=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
