"""Deterministic ecological report generation core."""

from .pipeline import build_report_ir
from .snapshot_report import build_report_ir_from_snapshot

__all__ = ["build_report_ir", "build_report_ir_from_snapshot"]
__version__ = "0.1.0"
