from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ProjectType(StrEnum):
    SOLAR = "solar"
    WIND = "wind"
    COAL = "coal"


@dataclass(frozen=True)
class Observation:
    project_type: ProjectType
    fid: int
    year: int
    values: dict[str, Any]
    source_row: int

    def number(self, field_name: str) -> float | None:
        value = self.values.get(field_name)
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except ValueError:
            return None

    def text(self, field_name: str) -> str:
        value = self.values.get(field_name)
        return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class TrendEvidence:
    indicator: str
    start_year: int
    end_year: int
    start_value: float
    end_value: float
    delta: float
    slope_per_year: float
    direction: str
    observation_count: int
    pattern: str = ""
    recent_direction: str = ""
    turning_years: tuple[int, ...] = ()
    minimum_value: float | None = None
    minimum_year: int | None = None
    maximum_value: float | None = None
    maximum_year: int | None = None


@dataclass(frozen=True)
class ProblemEvidence:
    problem: str
    source: str
    indicators: tuple[str, ...]
    available_trends: tuple[str, ...]


@dataclass(frozen=True)
class DataScope:
    ca_landcover_type: str | None = None
    has_buffer_gradient: bool = False
    historical_abandoned_mine: bool | None = None
    has_gangue_or_tailings: bool | None = None
    has_open_pit_or_subsidence: bool | None = None


@dataclass(frozen=True)
class ProblemDiagnosis:
    problem: str
    indicators: tuple[str, ...]
    status: str
    evidence_level: str
    adverse_indicators: tuple[str, ...]
    improving_indicators: tuple[str, ...]
    stable_indicators: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class MeasureDecision:
    text: str
    accepted: bool
    reason: str
    source: str


@dataclass(frozen=True)
class SelectedMeasure:
    problem: str
    text: str
    match_type: str
    source: str


@dataclass
class ReportIR:
    schema_version: str
    project_type: str
    fid: int
    target_year: int
    source_sheet: str
    source_row: int
    project_name: str | None
    years: list[int]
    latest_metrics: dict[str, float | int | str | None]
    trends: list[TrendEvidence]
    risk_level: str
    risk_level_code: int | None
    reported_risk_types: list[str]
    problems: list[ProblemEvidence]
    diagnoses: list[ProblemDiagnosis]
    data_scope: DataScope
    accepted_measures: list[str]
    selected_measures: list[SelectedMeasure]
    measure_audit: list[MeasureDecision]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
