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
    parser.add_argument("--generated-date")
    parser.add_argument("--prepared-by", default="生态状况评估编制组")
    parser.add_argument("--writer-mode", choices=("template", "llm"), default="template")
    parser.add_argument("--writer-base-url")
    parser.add_argument("--writer-model")
    parser.add_argument("--writer-api-key-env", default="ECO_REPORT_WRITER_API_KEY")
    parser.add_argument("--analysis-mode", choices=("seed", "llm"))
    parser.add_argument("--analysis-base-url")
    parser.add_argument("--analysis-model")
    parser.add_argument("--analysis-api-key-env")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = build_mvp_review_bundle(
        args.input,
        args.project,
        args.fid,
        args.year,
        args.project_name,
        args.output_dir,
        generated_date=args.generated_date,
        prepared_by=args.prepared_by,
        writer_mode=args.writer_mode,
        writer_base_url=args.writer_base_url,
        writer_model=args.writer_model,
        writer_api_key_env=args.writer_api_key_env,
        analysis_mode=args.analysis_mode,
        analysis_base_url=args.analysis_base_url,
        analysis_model=args.analysis_model,
        analysis_api_key_env=args.analysis_api_key_env,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
emExit(main())
