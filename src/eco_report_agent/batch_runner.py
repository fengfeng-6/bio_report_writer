from __future__ import annotations

import csv
import json
import multiprocessing
import os
import socket
import subprocess
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

from .analysis_builder import CROSS_ANALYSIS_PROMPT, PROBLEM_ANALYSIS_PROMPT
from .checkpointing import atomic_write_json, atomic_write_text, read_json, value_sha256
from .llm_http import FatalLLMError
from .llm_writer import SYSTEM_PROMPT
from .matching_results import attach_matching_results
from .mvp import load_json_config
from .raw_batch import prepare_raw_batch
from .snapshot_report import build_snapshot_bundle


HARD_MAX_CONCURRENCY = 30
BATCH_PIPELINE_VERSION = "2.4-mvp-batch-2-strict-llm-cls-map"
SMOKE_KEYS = ("solar-0000", "wind-0042", "coal-0033")
PILOT_KEYS = (
    "solar-0000", "solar-0001", "solar-0003", "solar-0004", "solar-0020",
    "solar-0026", "solar-0114", "solar-0150", "solar-0200", "solar-0228",
    "wind-0001", "wind-0002", "wind-0003", "wind-0005", "wind-0042",
    "wind-0199", "wind-0251", "wind-0373", "wind-0500", "wind-0503",
    "coal-0000", "coal-0003", "coal-0005", "coal-0006", "coal-0019",
    "coal-0033", "coal-0056", "coal-0078", "coal-0093", "coal-0094",
)


@dataclass(frozen=True)
class BatchRunConfig:
    writer_base_url: str
    writer_model: str
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY"
    generated_date: str | None = None
    prepared_by: str = "生态状况评估编制组"
    resume: bool = False


class BatchLock:
    def __init__(self, path: Path, resume: bool):
        self.path = path
        self.resume = resume

    def __enter__(self) -> "BatchLock":
        if self.path.exists():
            existing = read_json(self.path)
            same_host = existing.get("host") == socket.gethostname()
            pid = int(existing.get("pid", -1))
            running = same_host and pid > 0 and Path(f"/proc/{pid}").exists()
            old_job = str(existing.get("slurm_job_id") or "")
            old_job_running = False
            if old_job and not same_host:
                completed = subprocess.run(
                    ["squeue", "-h", "-j", old_job],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                old_job_running = bool(completed.stdout.strip())
            if running or old_job_running or not self.resume:
                raise RuntimeError(f"batch lock is active: {self.path}")
            self.path.unlink()
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
            "created_epoch": time.time(),
        }
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            payload = read_json(self.path)
            if payload.get("pid") == os.getpid():
                self.path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass


def _project_state_path(project_directory: Path) -> Path:
    return project_directory / "state.json"


def _run_project(
    entry: dict[str, Any],
    snapshot_root: str,
    projects_root: str,
    config: dict[str, Any],
    render_semaphore: Any,
) -> dict[str, Any]:
    project_directory = Path(projects_root) / entry["project_type"] / f"fid-{entry['fid']:04d}"
    project_directory.mkdir(parents=True, exist_ok=True)
    started = time.time()
    atomic_write_json(
        _project_state_path(project_directory),
        {
            "project_key": entry["project_key"],
            "status": "running",
            "started_epoch": started,
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        },
    )
    try:
        manifest = build_snapshot_bundle(
            Path(snapshot_root) / entry["snapshot_path"],
            project_directory,
            writer_base_url=config["writer_base_url"],
            writer_model=config["writer_model"],
            api_key_env=config["api_key_env"],
            generated_date=config.get("generated_date"),
            prepared_by=config["prepared_by"],
            resume=bool(config["resume"]),
            render_semaphore=render_semaphore,
        )
        result = {
            "project_key": entry["project_key"],
            "project_type": entry["project_type"],
            "fid": entry["fid"],
            "status": "succeeded",
            "elapsed_seconds": round(time.time() - started, 3),
            "manifest": str(project_directory / "result" / "manifest.json"),
            "validation_valid": manifest["validation"]["valid"],
            "llm_logical_requests": manifest["validation"].get(
                "llm_logical_requests", 0
            ),
            "llm_retry_attempts": manifest["validation"].get(
                "llm_retry_attempts", 0
            ),
            "api_retry_rate": manifest["validation"].get("api_retry_rate", 0.0),
        }
    except FatalLLMError as exc:
        result = {
            "project_key": entry["project_key"],
            "project_type": entry["project_type"],
            "fid": entry["fid"],
            "status": "paused",
            "fatal": True,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "elapsed_seconds": round(time.time() - started, 3),
        }
    except Exception as exc:
        result = {
            "project_key": entry["project_key"],
            "project_type": entry["project_type"],
            "fid": entry["fid"],
            "status": "failed",
            "fatal": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "elapsed_seconds": round(time.time() - started, 3),
        }
    result["run_fingerprint"] = entry.get("_run_fingerprint")
    atomic_write_json(_project_state_path(project_directory), result)
    return result


def _ordered_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {item["project_key"]: item for item in entries}
    ordered_keys = [
        *[key for key in SMOKE_KEYS if key in by_key],
        *[key for key in PILOT_KEYS if key in by_key and key not in SMOKE_KEYS],
    ]
    ordered_keys.extend(sorted(set(by_key) - set(ordered_keys)))
    return [by_key[key] for key in ordered_keys]


def _phase_plan(
    total: int,
    ramp: tuple[int, ...],
    *,
    include_smoke: bool = True,
) -> list[tuple[int, int]]:
    workers = tuple(min(HARD_MAX_CONCURRENCY, value) for value in ramp)
    if not workers or any(value < 1 for value in workers):
        raise ValueError("ramp must contain positive concurrency values")
    sizes = (3, 27, 60, 120) if include_smoke else (27, 60, 120)
    plan: list[tuple[int, int]] = []
    remaining = total
    for index, size in enumerate(sizes):
        if remaining <= 0:
            break
        phase_size = min(size, remaining)
        if include_smoke and index == 0:
            phase_workers = min(3, workers[0])
        else:
            worker_index = index - 1 if include_smoke else index
            phase_workers = workers[min(worker_index, len(workers) - 1)]
        plan.append((phase_size, phase_workers))
        remaining -= phase_size
    if remaining:
        plan.append((remaining, workers[-1]))
    return plan


def _should_enqueue(
    previous: dict[str, Any] | None,
    retry_failed: bool,
    input_changed: bool = False,
) -> bool:
    if not previous:
        return True
    status = previous.get("status")
    if status == "succeeded":
        return input_changed
    if status == "failed" and not retry_failed:
        return False
    return True


def _pipeline_dependency_fingerprint(
    *,
    writer_base_url: str,
    writer_model: str,
    generated_date: str | None,
    prepared_by: str,
) -> str:
    return value_sha256(
        {
            "pipeline_version": BATCH_PIPELINE_VERSION,
            "writer_base_url": writer_base_url,
            "writer_model": writer_model,
            "generated_date": generated_date,
            "prepared_by": prepared_by,
            "problem_prompt": value_sha256(PROBLEM_ANALYSIS_PROMPT),
            "cross_prompt": value_sha256(CROSS_ANALYSIS_PROMPT),
            "writer_prompt": value_sha256(SYSTEM_PROMPT),
            "trend_config": value_sha256(load_json_config("trend_config.json")),
            "problem_rules": value_sha256(load_json_config("problem_rules.json")),
            "analysis_config": value_sha256(load_json_config("analysis_config.json")),
            "report_style": value_sha256(load_json_config("report_style_config.json")),
        }
    )


def _write_summaries(root: Path, results: dict[str, dict[str, Any]], status: str) -> None:
    ordered = [results[key] for key in sorted(results)]
    counts = Counter(item["status"] for item in ordered)
    atomic_write_json(
        root / "progress.json",
        {
            "status": status,
            "counts": dict(sorted(counts.items())),
            "updated_epoch": time.time(),
            "projects": ordered,
        },
    )
    failures = [item for item in ordered if item["status"] in {"failed", "paused"}]
    atomic_write_json(root / "failures.json", failures)
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "project_key", "project_type", "fid", "status",
            "elapsed_seconds", "validation_valid", "error_type", "error",
            "llm_logical_requests", "llm_retry_attempts", "api_retry_rate",
            "run_fingerprint",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(ordered)
    atomic_write_text(root / "summary.csv", buffer.getvalue())


def _slurm_job_end_epoch() -> float | None:
    raw_end = os.environ.get("SLURM_JOB_END_TIME", "").strip()
    if raw_end:
        try:
            return float(raw_end)
        except ValueError:
            pass
    job_id = os.environ.get("SLURM_JOB_ID", "").strip()
    if not job_id:
        return None
    try:
        completed = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%e"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    values = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not values or values[0] in {"N/A", "Unknown"}:
        return None
    try:
        return datetime.fromisoformat(values[0]).timestamp()
    except ValueError:
        return None


def _load_existing_results(root: Path) -> dict[str, dict[str, Any]]:
    progress = root / "progress.json"
    if not progress.is_file():
        return {}
    payload = read_json(progress)
    return {
        item["project_key"]: item
        for item in payload.get("projects", [])
        if isinstance(item, dict) and item.get("project_key")
    }


def run_batch(
    *,
    workbook_path: str | Path,
    matching_results_path: str | Path | None = None,
    output_root: str | Path,
    batch_id: str,
    target_year: int,
    writer_base_url: str | None,
    writer_model: str | None,
    api_key_env: str = "ECO_REPORT_WRITER_API_KEY",
    prepare_only: bool = False,
    resume: bool = False,
    retry_failed: bool = False,
    max_concurrency: int = 30,
    requests_per_minute: int = 30,
    ramp: tuple[int, ...] = (5, 10, 20, 30),
    render_concurrency: int = 4,
    project_type: str | None = None,
    fids: set[int] | None = None,
    project_keys: set[str] | None = None,
    project_names: Mapping[str, str] | None = None,
    generated_date: str | None = None,
    prepared_by: str = "生态状况评估编制组",
    stop_before_end_seconds: int = 2700,
) -> dict[str, Any]:
    if not 1 <= requests_per_minute <= 30:
        raise ValueError("requests_per_minute must be between 1 and 30")
    if not 1 <= max_concurrency <= HARD_MAX_CONCURRENCY:
        raise ValueError("max_concurrency must be between 1 and 30")
    if not 1 <= render_concurrency <= max_concurrency:
        raise ValueError("render_concurrency must be between 1 and max_concurrency")
    if any(value > max_concurrency for value in ramp):
        raise ValueError("ramp cannot exceed max_concurrency")
    root = Path(output_root) / batch_id
    if root.exists() and not resume:
        raise FileExistsError(f"batch already exists; use --resume: {root}")
    root.mkdir(parents=True, exist_ok=True)
    with BatchLock(root / "batch.lock", resume=resume):
        snapshot_root = root / "input_snapshot"
        manifest = prepare_raw_batch(
            workbook_path,
            snapshot_root,
            target_year,
            resume=resume,
            project_names=project_names,
        )
        if matching_results_path is not None:
            manifest = attach_matching_results(
                snapshot_root, manifest, matching_results_path, target_year)
        effective_generated_date = generated_date or date.today().isoformat()
        batch_path = root / "batch.json"
        batch_value = {
                "batch_id": batch_id,
                "target_year": target_year,
                "source_workbook_sha256": manifest["source_workbook_sha256"],
                "max_concurrency": max_concurrency,
                "ramp": list(ramp),
                "render_concurrency": render_concurrency,
                "project_count": manifest["project_count"],
                "project_counts": manifest["project_counts"],
                "generated_date": effective_generated_date,
        }
        if batch_path.is_file():
            existing_batch = read_json(batch_path)
            for name in ("batch_id", "target_year", "source_workbook_sha256"):
                if existing_batch.get(name) != batch_value[name]:
                    raise ValueError(f"batch immutable field changed: {name}")
            existing_date = existing_batch.get("generated_date")
            if existing_date:
                if generated_date and generated_date != existing_date:
                    raise ValueError("batch immutable field changed: generated_date")
                effective_generated_date = str(existing_date)
            else:
                existing_batch["generated_date"] = effective_generated_date
                atomic_write_json(batch_path, existing_batch)
        else:
            atomic_write_json(batch_path, batch_value)
        if prepare_only:
            result = {
                "status": "prepared",
                "batch_id": batch_id,
                "project_count": manifest["project_count"],
                "project_counts": manifest["project_counts"],
            }
            atomic_write_json(root / "progress.json", result)
            return result
        if not writer_base_url or not writer_model:
            raise ValueError("writer_base_url and writer_model are required")

        entries = manifest["projects"]
        if project_type:
            entries = [item for item in entries if item["project_type"] == project_type]
        if fids is not None:
            entries = [item for item in entries if int(item["fid"]) in fids]
        if project_keys is not None:
            entries = [item for item in entries if item["project_key"] in project_keys]
        entries = _ordered_entries(entries)
        results = _load_existing_results(root) if resume else {}
        dependency_fingerprint = _pipeline_dependency_fingerprint(
            writer_base_url=writer_base_url,
            writer_model=writer_model,
            generated_date=effective_generated_date,
            prepared_by=prepared_by,
        )
        pending = []
        for entry in entries:
            entry["_run_fingerprint"] = value_sha256(
                {
                    "pipeline": dependency_fingerprint,
                    "snapshot": entry["snapshot_sha256"],
                }
            )
            previous = results.get(entry["project_key"])
            previous_fingerprint = previous.get("run_fingerprint") if previous else None
            dependency_changed = bool(
                previous_fingerprint
                and previous_fingerprint != entry["_run_fingerprint"]
            )
            if _should_enqueue(
                previous,
                retry_failed,
                input_changed=bool(entry.get("_name_changed")) or dependency_changed,
            ):
                pending.append(entry)

        config = asdict(
            BatchRunConfig(
                writer_base_url=writer_base_url,
                writer_model=writer_model,
                api_key_env=api_key_env,
                generated_date=effective_generated_date,
                prepared_by=prepared_by,
                resume=resume,
            )
        )
        os.environ["ECO_REPORT_REQUESTS_PER_MINUTE"] = str(requests_per_minute)
        os.environ["ECO_REPORT_RATE_STATE"] = str((root / "request_rate.json").resolve())
        end_epoch = _slurm_job_end_epoch()
        paused = False
        error_signatures: Counter[str] = Counter()
        cursor = 0
        with multiprocessing.Manager() as manager:
            render_semaphore = manager.BoundedSemaphore(render_concurrency)
            pending_starts_with_smoke = tuple(
                item["project_key"] for item in pending[: len(SMOKE_KEYS)]
            ) == SMOKE_KEYS
            for phase_size, phase_workers in _phase_plan(
                len(pending), ramp, include_smoke=pending_starts_with_smoke
            ):
                phase = pending[cursor:cursor + phase_size]
                cursor += phase_size
                phase_results: list[dict[str, Any]] = []
                with ProcessPoolExecutor(max_workers=phase_workers) as executor:
                    active: dict[Any, dict[str, Any]] = {}
                    iterator = iter(phase)

                    def submit_next() -> bool:
                        nonlocal paused
                        if paused:
                            return False
                        if end_epoch and time.time() >= end_epoch - stop_before_end_seconds:
                            paused = True
                            return False
                        try:
                            entry = next(iterator)
                        except StopIteration:
                            return False
                        future = executor.submit(
                            _run_project,
                            entry,
                            str(snapshot_root),
                            str(root / "projects"),
                            config,
                            render_semaphore,
                        )
                        active[future] = entry
                        return True

                    for _ in range(min(phase_workers, len(phase))):
                        submit_next()
                    while active:
                        done, _ = wait(active, return_when=FIRST_COMPLETED)
                        for future in done:
                            active.pop(future)
                            item = future.result()
                            phase_results.append(item)
                            results[item["project_key"]] = item
                            if item.get("fatal"):
                                paused = True
                            _write_summaries(root, results, "running")
                            submit_next()
                if paused or (
                    end_epoch and time.time() >= end_epoch - stop_before_end_seconds
                ):
                    break

        selected_keys = {item["project_key"] for item in entries}
        selected_results = [results[key] for key in selected_keys if key in results]
        succeeded = sum(item["status"] == "succeeded" for item in selected_results)
        failed = sum(
            item["status"] in {"failed", "paused"} for item in selected_results
        )
        pending_count = len(selected_keys) - succeeded - failed
        if paused or succeeded < len(selected_keys) and cursor < len(pending):
            status = "paused"
        elif succeeded == len(selected_keys):
            status = "complete"
        else:
            status = "complete_with_failures"
        _write_summaries(root, results, status)
        return {
            "status": status,
            "batch_id": batch_id,
            "selected_project_count": len(selected_keys),
            "succeeded": succeeded,
            "failed": failed,
            "pending": pending_count,
        }
