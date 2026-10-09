#!/usr/bin/env python3
"""Load an exported two-table report bundle into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_bundle(bundle: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("failure_count") != 0:
        raise ValueError("bundle contains export failures")
    reports = read_ndjson(bundle / "report_entry.ndjson")
    charts = read_ndjson(bundle / "report_chart.ndjson")
    if len(reports) != manifest.get("report_count") or len(charts) != manifest.get("chart_count"):
        raise ValueError("bundle record counts do not match manifest")
    report_ids = {row["id"] for row in reports}
    if any(row["report_id"] not in report_ids for row in charts):
        raise ValueError("chart references a report outside the bundle")
    return reports, charts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=Path("sql/postgres_report_schema.sql"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    reports, charts = load_bundle(args.bundle_dir)
    if args.dry_run:
        print(json.dumps({"report_count": len(reports), "chart_count": len(charts), "valid": True}))
        return 0
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is required: pip install 'psycopg[binary]>=3.1'") from exc
    ddl = args.schema.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(ddl)
            cursor.executemany(
                """INSERT INTO report_entry (id, content, type) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, type = EXCLUDED.type""",
                [(row["id"], row["content"], row["type"]) for row in reports],
            )
            cursor.executemany(
                """INSERT INTO report_chart (report_id, insert_key, type, data_json)
                   VALUES (%s, %s, %s, %s::jsonb)
                   ON CONFLICT (report_id, insert_key) DO UPDATE
                   SET type = EXCLUDED.type, data_json = EXCLUDED.data_json""",
                [(row["report_id"], row["insert_key"], row["type"], json.dumps(row["data_json"], ensure_ascii=False)) for row in charts],
            )
    print(json.dumps({"report_count": len(reports), "chart_count": len(charts), "imported": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
