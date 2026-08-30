from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from eco_report_agent.dify_deploy import build_import_payload, import_workflow


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a validated workflow to Dify 1.16.1")
    parser.add_argument("--dsl", type=Path, default=Path("dify/eco_report_workflow.yml"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--app-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dsl_text = args.dsl.read_text(encoding="utf-8")
    payload = build_import_payload(dsl_text, args.app_id)
    digest = hashlib.sha256(dsl_text.encode("utf-8")).hexdigest()
    endpoint = args.base_url.rstrip("/") + "/console/api/apps/imports"
    if not args.apply:
        print(f"dry_run=true endpoint={endpoint} dsl_sha256={digest} mode={payload['mode']}")
        return 0

    token = os.environ.get("DIFY_CONSOLE_ACCESS_TOKEN", "")
    csrf_token = os.environ.get("DIFY_CONSOLE_CSRF_TOKEN", "")
    result = import_workflow(
        args.base_url,
        token,
        dsl_text,
        args.app_id,
        csrf_token=csrf_token,
    )
    status = result.body.get("status", "unknown")
    app_id = result.body.get("app_id", "")
    import_id = result.body.get("id", "")
    print(
        f"http_status={result.status_code} import_status={status} "
        f"app_id={app_id} import_id={import_id} dsl_sha256={digest}"
    )
    return 0 if result.status_code in {200, 202} else 1


if __name__ == "__main__":
    raise SystemExit(main())
