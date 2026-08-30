from __future__ import annotations

import argparse
from pathlib import Path

from eco_report_agent.dify_dsl import DIFY_TARGET_VERSION, render_dsl


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Build Dify {DIFY_TARGET_VERSION} workflow DSL")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("dify/eco_report_workflow.template.yml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dify/eco_report_workflow.yml"),
    )
    parser.add_argument(
        "--api-base-url",
        default="http://eco-report-smoke-api:8000",
    )
    args = parser.parse_args()
    content = render_dsl(args.template, args.api_base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"generated={args.output} target_dify={DIFY_TARGET_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
