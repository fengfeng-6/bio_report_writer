from __future__ import annotations

import argparse
import json
from pathlib import Path

from eco_report_agent.batch_runner import run_batch


def _integers(value: str) -> set[int]:
    try:
        return {int(item.strip()) for item in value.split(",") if item.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fids must be comma-separated integers") from exc


def _ramp(value: str) -> tuple[int, ...]:
    values = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    if not values:
        raise argparse.ArgumentTypeError("ramp cannot be empty")
    return values


def _strings(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, run, or resume the raw-workbook MVP report batch"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--matching-input", type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/batches"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=30)
    parser.add_argument("--requests-per-minute", type=int, default=30)
    parser.add_argument("--ramp", type=_ramp, default=(5, 10, 20, 30))
    parser.add_argument("--render-concurrency", type=int, default=4)
    parser.add_argument("--project-type", choices=("solar", "wind", "coal"))
    parser.add_argument("--fids", type=_integers)
    parser.add_argument("--project-keys", type=_strings)
    parser.add_argument(
        "--project-name-map",
        type=Path,
        help="UTF-8 JSON object mapping project keys (for example solar-0026) to names",
    )
    parser.add_argument("--writer-base-url")
    parser.add_argument("--writer-model")
    parser.add_argument("--api-key-env", default="ECO_REPORT_WRITER_API_KEY")
    parser.add_argument("--generated-date")
    parser.add_argument("--prepared-by", default="生态状况评估编制组")
    parser.add_argument("--stop-before-end-seconds", type=int, default=2700)
    args = parser.parse_args()
    project_names = None
    if args.project_name_map is not None:
        project_names = json.loads(args.project_name_map.read_text(encoding="utf-8"))
        if not isinstance(project_names, dict):
            parser.error("--project-name-map must contain a JSON object")
    result = run_batch(
        workbook_path=args.input,
        matching_results_path=args.matching_input,
        output_root=args.output_root,
        batch_id=args.batch_id,
        target_year=args.year,
        writer_base_url=args.writer_base_url,
        writer_model=args.writer_model,
        api_key_env=args.api_key_env,
        prepare_only=args.prepare_only,
        resume=args.resume,
        retry_failed=args.retry_failed,
        max_concurrency=args.max_concurrency,
        requests_per_minute=args.requests_per_minute,
        ramp=args.ramp,
        render_concurrency=args.render_concurrency,
        project_type=args.project_type,
        fids=args.fids,
        project_keys=args.project_keys,
        project_names=project_names,
        generated_date=args.generated_date,
        prepared_by=args.prepared_by,
        stop_before_end_seconds=args.stop_before_end_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"prepared", "complete"} else 3 if result["status"] == "paused" else 2


if __name__ == "__main__":
    raise SystemExit(main())
