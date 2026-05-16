"""Statistical safety filter — linear regression on the ACWR trend.

Project Plan §2: "kerge reeglipõhine / statistiline moodul (scikit-learn
lineaarne regressioon) ACWR-i ajalise trendi prognoosiks; töötab LLM-i kõrval
otsuste 'turvafiltrina'."

Idea: even when today's ACWR is in the sweet spot (e.g. 1.15), the *trend*
over the last 14 days may show it climbing 0.05/day → in 7 days it would be
1.5 (danger zone). A linear-regression-derived 7-day projection lets the
safety filter raise a warning *before* the threshold is crossed, not after.

This is a deliberately small model — one feature (day-of-period), one target
(ACWR), no regularisation. It exists so the UI can show:

    "ACWR 1.15 today, but climbing 0.04/day → projection 1.43 in 7 days."

We use sklearn.linear_model.LinearRegression for the regression fit and
sklearn.metrics.r2_score for trend-confidence scoring. The fit is over the
last `lookback_days` of ACWR observations (default 14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

from .load import ACWR_DANGER_HIGH


@dataclass(frozen=True)
class ACWRForecast:
    """Linear-regression projection of ACWR `horizon_days` into the future.

    Attributes:
        slope_per_day: ACWR change per day (positive = climbing toward danger).
        intercept: regression intercept.
        r_squared: goodness-of-fit; below ~0.3 the trend is too noisy to act on.
        projected_acwr: predicted ACWR at `as_of + horizon_days`.
        crosses_danger_in_days: how many days until the projection crosses
            ACWR_DANGER_HIGH (1.5). `None` if it doesn't cross within 30 days.
    """

    slope_per_day: float
    intercept: float
    r_squared: float
    projected_acwr: float
    horizon_days: int
    crosses_danger_in_days: int | None
    n_observations: int


def forecast_acwr(
    acwr_series: pd.Series,
    *,
    lookback_days: int = 14,
    horizon_days: int = 7,
) -> ACWRForecast | None:
    """Fit a linear regression on the last `lookback_days` of ACWR observations.

    Returns None if there aren't enough non-null observations (need ≥ 7).

    `acwr_series` is expected to be the `acwr` column from
    `vorm.metrics.load.acwr_series()` — indexed by date, possibly with NaN
    rows. We drop NaN before fitting, so 7-day cold-start gaps are handled.
    """
    if acwr_series.empty:
        return None

    recent = acwr_series.tail(lookback_days).dropna()
    if len(recent) < 7:
        return None

    # X = days since the first observation in the lookback window
    days = np.arange(len(recent)).reshape(-1, 1).astype(float)
    y = recent.to_numpy(dtype=float)

    model = LinearRegression().fit(days, y)
    slope = float(model.coef_[0])
    intercept = float(model.intercept_)
    y_pred = model.predict(days)
    r2 = float(r2_score(y, y_pred))

    last_x = float(len(recent) - 1)
    projected = float(model.predict(np.array([[last_x + horizon_days]]))[0])

    crosses_in: int | None = None
    if slope > 0:
        # current value at the last observed day
        current = float(y_pred[-1])
        if current < ACWR_DANGER_HIGH:
            days_to_cross = (ACWR_DANGER_HIGH - current) / slope
            if 0 < days_to_cross <= 30:
                crosses_in = int(round(days_to_cross))

    return ACWRForecast(
        slope_per_day=slope,
        intercept=intercept,
        r_squared=r2,
        projected_acwr=projected,
        horizon_days=horizon_days,
        crosses_danger_in_days=crosses_in,
        n_observations=len(recent),
    )


def forecast_message(forecast: ACWRForecast | None) -> str | None:
    """Human-readable summary for the UI / LLM prompt context. None when
    there's nothing actionable to say (too few points or trend too noisy)."""
    if forecast is None:
        return None
    if forecast.n_observations < 7 or forecast.r_squared < 0.25:
        return None  # noisy fit — don't make a confident-sounding claim
    direction = "tõuseb" if forecast.slope_per_day > 0 else "langeb"
    parts = [
        f"ACWR trend: {direction} {abs(forecast.slope_per_day):.3f}/päevas "
        f"(R²={forecast.r_squared:.2f}, n={forecast.n_observations})",
        f"prognoos {forecast.horizon_days} päeva pärast: "
        f"ACWR ≈ {forecast.projected_acwr:.2f}",
    ]
    if forecast.crosses_danger_in_days is not None:
        parts.append(
            f"⚠ jätkudes ületab ohulõike (1.5) {forecast.crosses_danger_in_days} päeva pärast"
        )
    return " · ".join(parts)
