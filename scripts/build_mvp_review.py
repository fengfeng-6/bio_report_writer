from __future__ import annotations

import argparse
from pathlib import Path

from eco_report_agent.mvp_bundle import build_mvp_review_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--project", required=True, choices=("coal", "solar", "wind"))
    parser.add_argument("--fid", required=True, type=int)
    parser.add_argument("--year", type=int)
    parser.add_argument("--project-name")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = build_mvp_review_bundle(
        args.input,
        args.project,
        args.fid,
        args.year,
        args.project_name,
        args.output_dir,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
