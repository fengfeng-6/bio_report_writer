from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .models import ReportIR
from .report_plan import FIXED_HEADINGS, FORBIDDEN_SECTIONS


@dataclass(frozen=True)
class ReportIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ReportValidationResult:
    valid: bool
    issues: tuple[ReportIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "issues": [asdict(issue) for issue in self.issues]}


_PARAMETER_PATTERN = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*(?:米|公里|公顷|亩|吨|千克|公斤|万元|元|天|个月|年工期|株|克/平方米)"
)
_EXAGGERATED = ("极其严重", "非常危险", "灾难性", "严重失控")


def validate_report_markdown(text: str, report: ReportIR) -> ReportValidationResult:
    issues: list[ReportIssue] = []
    for heading in FIXED_HEADINGS:
        if not re.search(rf"^#{{2,4}}\s+{re.escape(heading)}\s*$", text, flags=re.MULTILINE):
            issues.append(ReportIssue("missing_heading", heading))
    for section in FORBIDDEN_SECTIONS:
        if section in text:
            issues.append(ReportIssue("forbidden_section", section))
    for phrase in _EXAGGERATED:
        if phrase in text:
            issues.append(ReportIssue("exaggerated_language", phrase))
    for match in _PARAMETER_PATTERN.finditer(text):
        issues.append(ReportIssue("invented_implementation_parameter", match.group(0)))
    for item in report.measure_audit:
        if not item.accepted and item.reason != "duplicate" and item.text in text:
            issues.append(ReportIssue("rejected_measure_present", item.text))
    unsupported = [
        warning.removeprefix("unsupported_reported_risk_type:")
        for warning in report.warnings
        if warning.startswith("unsupported_reported_risk_type:")
    ]
    for risk in unsupported:
        if risk in text:
            issues.append(ReportIssue("unsupported_risk_present", risk))
    if re.search(r"(?:由于|因).{0,40}(?:导致|造成)", text):
        issues.append(ReportIssue("unsupported_causal_claim", "检测到未经证据约束的因果句式"))
    return ReportValidationResult(valid=not issues, issues=tuple(issues))
