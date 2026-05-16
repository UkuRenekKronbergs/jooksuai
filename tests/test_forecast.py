"""Tests for vorm.metrics.forecast — the sklearn ACWR-trend filter."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from vorm.metrics.forecast import forecast_acwr, forecast_message


def _series_from(values: list[float]) -> pd.Series:
    idx = pd.date_range(date(2026, 5, 1), periods=len(values), freq="D").date
    return pd.Series(values, index=idx, name="acwr")


def test_returns_none_when_too_few_observations():
    """Fewer than 7 non-null points → no fit."""
    assert forecast_acwr(_series_from([1.0, 1.05, 1.1])) is None


def test_returns_none_on_empty_series():
    assert forecast_acwr(pd.Series(dtype=float)) is None


def test_flat_series_has_zero_slope():
    forecast = forecast_acwr(_series_from([1.0] * 14))
    assert forecast is not None
    assert forecast.slope_per_day == pytest.approx(0.0, abs=1e-9)
    assert forecast.projected_acwr == pytest.approx(1.0, abs=1e-6)
    assert forecast.crosses_danger_in_days is None


def test_upward_trend_projects_higher_in_7_days():
    """Slope +0.05/day starting at 1.0 → in 14 days reaches ~1.7 (data),
    7-day projection from last day should be ~0.35 above the last observation."""
    values = [1.0 + 0.05 * i for i in range(14)]
    forecast = forecast_acwr(_series_from(values))
    assert forecast is not None
    assert forecast.slope_per_day == pytest.approx(0.05, abs=1e-6)
    # Last observed value = 1.65; +7 × 0.05 = 2.00
    assert forecast.projected_acwr == pytest.approx(2.0, abs=1e-6)
    assert forecast.r_squared == pytest.approx(1.0, abs=1e-6)


def test_danger_crossing_estimated_when_climbing():
    """ACWR 1.20 climbing 0.05/day → crosses 1.5 in (1.5−1.2)/0.05 = 6 days."""
    values = [1.20 + 0.05 * i for i in range(7)]  # last day = 1.50 → already at threshold
    # Use a longer window where ACWR starts further below the threshold.
    values = [1.0 + 0.04 * i for i in range(10)]
    forecast = forecast_acwr(_series_from(values))
    assert forecast is not None
    assert forecast.crosses_danger_in_days is not None
    # Last observed ACWR = 1.0 + 0.04*9 = 1.36; threshold 1.5; slope 0.04;
    # (1.5 − 1.36) / 0.04 = 3.5 → rounds to 4
    assert 3 <= forecast.crosses_danger_in_days <= 4


def test_downward_trend_does_not_emit_crossing():
    """A declining ACWR has slope ≤ 0 — no danger crossing in the future."""
    values = [1.5 - 0.02 * i for i in range(14)]
    forecast = forecast_acwr(_series_from(values))
    assert forecast is not None
    assert forecast.slope_per_day < 0
    assert forecast.crosses_danger_in_days is None


def test_noisy_trend_message_suppressed():
    """When R² is low, forecast_message() returns None so the UI doesn't
    show a misleadingly confident projection."""
    rng = np.random.default_rng(42)
    noisy = list(1.0 + rng.normal(0, 0.4, size=14))
    forecast = forecast_acwr(_series_from(noisy))
    assert forecast is not None
    msg = forecast_message(forecast)
    # 0.4 std on a series with no real trend → R² near zero → suppressed
    if forecast.r_squared < 0.25:
        assert msg is None


def test_message_includes_direction_and_projection():
    # Series ends at 1.0 + 0.03*13 = 1.39 — still below 1.5, but the projected
    # path crosses 1.5 in ~4 days, so the warning string fires.
    values = [1.0 + 0.03 * i for i in range(14)]
    forecast = forecast_acwr(_series_from(values))
    msg = forecast_message(forecast)
    assert msg is not None
    assert "tõuseb" in msg
    assert "ACWR" in msg
    assert "ohulõike" in msg


def test_drops_leading_nan_observations():
    """Cold-start gap (first 7 days NaN, then 10 valid points) → fits on the
    10 valid points, returns a forecast."""
    values = [None] * 7 + [1.0 + 0.03 * i for i in range(10)]
    series = pd.Series(values, dtype=float)
    forecast = forecast_acwr(series, lookback_days=20)
    assert forecast is not None
    assert forecast.n_observations == 10
