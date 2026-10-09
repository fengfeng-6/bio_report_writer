from __future__ import annotations

from collections.abc import Iterable

from .models import Observation, TrendEvidence


TREND_FIELDS = (
    "MEAN_NDVI",
    "MEAN_NPP",
    "MEAN_NEP",
    "MEAN_NCS",
    "CA",
    "LPI",
    "NP",
    "PD",
    "ED",
    "LSI",
    "MEAN_VCI",
    "MEAN_EVI",
    "MEAN_SIF",
    "MEAN_VCS_C",
    "MEAN_VCS_CO2e",
    "MEAN_VCR",
    "ERI",
)

# Numeric stability band only; this is not an ecological severity threshold.
DEFAULT_RELATIVE_NOISE_TOLERANCE = 0.001


def linear_slope(points: Iterable[tuple[float, float]]) -> float:
    values = list(points)
    if len(values) < 2:
        raise ValueError("At least two points are required")
    mean_x = sum(x for x, _ in values) / len(values)
    mean_y = sum(y for _, y in values) / len(values)
    denominator = sum((x - mean_x) ** 2 for x, _ in values)
    if denominator == 0:
        raise ValueError("X values must not all be equal")
    return sum((x - mean_x) * (y - mean_y) for x, y in values) / denominator


def summarize_trend(
    series: list[Observation],
    indicator: str,
    relative_noise_tolerance: float = DEFAULT_RELATIVE_NOISE_TOLERANCE,
) -> TrendEvidence | None:
    points = [
        (item.year, value)
        for item in series
        if (value := item.number(indicator)) is not None
    ]
    if len(points) < 2:
        return None
    start_year, start_value = points[0]
    end_year, end_value = points[-1]
    slope = linear_slope((float(year), value) for year, value in points)
    scale = max(max(abs(value) for _, value in points), 1e-12)
    noise_band = scale * relative_noise_tolerance
    series_is_stable = max(value for _, value in points) - min(value for _, value in points) <= noise_band
    modeled_change = slope * (end_year - start_year)
    direction = (
        "stable"
        if series_is_stable or abs(modeled_change) <= noise_band
        else "increase"
        if slope > 0
        else "decrease"
    )
    raw_deltas = [points[index][1] - points[index - 1][1] for index in range(1, len(points))]
    deltas = [0.0 if abs(delta) <= noise_band else delta for delta in raw_deltas]
    signs = [1 if delta > 0 else -1 if delta < 0 else 0 for delta in deltas]
    nonzero_signs = [sign for sign in signs if sign]
    transitions = [
        index
        for index in range(1, len(nonzero_signs))
        if nonzero_signs[index] != nonzero_signs[index - 1]
    ]
    if series_is_stable:
        pattern = "stable"
    elif nonzero_signs and all(sign > 0 for sign in nonzero_signs):
        pattern = "overall_increase"
    elif nonzero_signs and all(sign < 0 for sign in nonzero_signs):
        pattern = "overall_decrease"
    elif len(transitions) == 1 and nonzero_signs[0] < 0:
        pattern = "decrease_then_increase"
    elif len(transitions) == 1 and nonzero_signs[0] > 0:
        pattern = "increase_then_decrease"
    elif direction == "increase":
        pattern = "fluctuating_increase"
    elif direction == "decrease":
        pattern = "fluctuating_decrease"
    else:
        pattern = "stable"
    recent_delta = deltas[-1]
    recent_direction = "increase" if recent_delta > 0 else "decrease" if recent_delta < 0 else "stable"
    turning_years = tuple(
        points[index + 1][0]
        for index in range(1, len(signs))
        if signs[index] and signs[index - 1] and signs[index] != signs[index - 1]
    )
    minimum_year, minimum_value = min(points, key=lambda item: item[1])
    maximum_year, maximum_value = max(points, key=lambda item: item[1])
    return TrendEvidence(
        indicator=indicator,
        start_year=start_year,
        end_year=end_year,
        start_value=start_value,
        end_value=end_value,
        delta=end_value - start_value,
        slope_per_year=slope,
        direction=direction,
        observation_count=len(points),
        pattern=pattern,
        recent_direction=recent_direction,
        turning_years=turning_years,
        minimum_value=minimum_value,
        minimum_year=minimum_year,
        maximum_value=maximum_value,
        maximum_year=maximum_year,
    )


def compute_vci(values: Iterable[float | None]) -> list[float | None]:
    source = list(values)
    available = [value for value in source if value is not None]
    if not available:
        return [None] * len(source)
    minimum = min(available)
    maximum = max(available)
    if maximum == minimum:
        return [None if value is None else 0.0 for value in source]
    return [
        None if value is None else (value - minimum) / (maximum - minimum) * 100.0
        for value in source
    ]


def compute_vcs(npp: float, tau: float, carbon_fraction: float = 0.45) -> tuple[float, float]:
    vcs_c = npp * tau * carbon_fraction
    return vcs_c, vcs_c * 44.0 / 12.0
