from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .smoke_fixture import build_smoke_workbook


RELEASE_ROOT = PurePosixPath("eco-report-agent")
STATIC_RELEASE_FILES = (
    Path("pyproject.toml"),
    Path("deploy/cloud/docker-compose.eco-report.yml"),
    Path("deploy/cloud/runtime.env.example"),
    Path("deploy/cloud/README.md"),
    Path("deploy/cloud/smoke_probe.py"),
    Path("dify/eco_report_workflow.yml"),
)


@dataclass(frozen=True)
class CloudRelease:
    path: Path
    sha256: str
    file_count: int


def _release_files(project_root: Path) -> list[Path]:
    source_files = sorted((project_root / "src" / "eco_report_agent").glob("*.py"))
    files = [project_root / relative for relative in STATIC_RELEASE_FILES]
    files.extend(source_files)
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.is_symlink():
            raise ValueError(f"Release inputs must not be symlinks: {path}")
    return sorted(files, key=lambda item: item.relative_to(project_root).as_posix())


def _tar_info(archive_name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(archive_name)
    info.size = size
    info.mode = 0o444
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_cloud_release(project_root: Path, output_path: Path) -> CloudRelease:
    root = project_root.resolve()
    files = _release_files(root)
    manifest_files: list[dict[str, object]] = []
    payloads: list[tuple[str, bytes]] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        payloads.append((relative, payload))
        manifest_files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )

    smoke_relative = "fixtures/cloud-smoke.xlsx"
    smoke_payload = build_smoke_workbook()
    payloads.append((smoke_relative, smoke_payload))
    manifest_files.append(
        {
            "path": smoke_relative,
            "sha256": hashlib.sha256(smoke_payload).hexdigest(),
            "size": len(smoke_payload),
            "synthetic_test_fixture": True,
        }
    )

    manifest = json.dumps(
        {
            "format_version": 1,
            "private_inputs_included": False,
            "cloud_mode": "synthetic-smoke-test-only",
            "files": manifest_files,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    payloads.append(("release-manifest.json", manifest))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative, payload in payloads:
                    name = (RELEASE_ROOT / PurePosixPath(relative)).as_posix()
                    archive.addfile(_tar_info(name, len(payload)), io.BytesIO(payload))

    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return CloudRelease(path=output_path, sha256=digest, file_count=len(payloads))
