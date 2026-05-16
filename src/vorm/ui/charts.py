"""Plotly charts for the Streamlit UI.

All functions return a `go.Figure`. The Streamlit app is responsible for
calling `st.plotly_chart` so these are easy to reuse from a notebook.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ..data.models import AthleteProfile, TrainingActivity
from ..metrics.load import (
    ACWR_DANGER_HIGH,
    ACWR_DANGER_LOW,
    ACWR_SWEET_SPOT,
    acwr_series,
    estimate_rpe_from_hr,
    fitness_form,
)
from ..metrics.personal_bests import progression_at_distance

_COLOR_SWEETSPOT = "rgba(76, 175, 80, 0.15)"
_COLOR_DANGER = "rgba(244, 67, 54, 0.12)"


def acwr_chart(daily_load: pd.Series) -> go.Figure:
    df = acwr_series(daily_load)
    fig = go.Figure()

    if not df.empty:
        x_min, x_max = df.index.min(), df.index.max()
        fig.add_hrect(
            y0=ACWR_SWEET_SPOT[0], y1=ACWR_SWEET_SPOT[1],
            fillcolor=_COLOR_SWEETSPOT, line_width=0, layer="below",
            annotation_text="Sweet-spot (0.8–1.3)", annotation_position="top left",
        )
        fig.add_hrect(
            y0=ACWR_DANGER_HIGH, y1=max(df["acwr"].max() or ACWR_DANGER_HIGH + 0.2, ACWR_DANGER_HIGH + 0.2),
            fillcolor=_COLOR_DANGER, line_width=0, layer="below",
            annotation_text=f"Ohupiir (≥ {ACWR_DANGER_HIGH})", annotation_position="top left",
        )
        fig.add_hrect(
            y0=0, y1=ACWR_DANGER_LOW,
            fillcolor=_COLOR_DANGER, line_width=0, layer="below",
            annotation_text=f"Alakoormus (≤ {ACWR_DANGER_LOW})", annotation_position="bottom left",
        )
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["acwr"],
                mode="lines+markers", name="ACWR",
                line=dict(color="#E55934", width=2),
                marker=dict(size=5),
            )
        )
        fig.update_xaxes(range=[x_min, x_max])

    fig.update_layout(
        title="ACWR (akuutne / krooniline koormus)",
        xaxis_title="Kuupäev",
        yaxis_title="ACWR",
        yaxis=dict(range=[0, 2.0]),
        height=360,
        margin=dict(l=40, r=20, t=60, b=40),
        hovermode="x unified",
    )
    return fig


def daily_load_chart(daily_load: pd.Series) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=daily_load.index, y=daily_load.values,
            name="Päevakoormus (TRIMP)",
            marker_color="#3A86FF",
        )
    )
    if not daily_load.empty:
        chronic = daily_load.rolling(window=28, min_periods=1).mean()
        acute = daily_load.rolling(window=7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=chronic.index, y=chronic.values,
                mode="lines", name="Krooniline (28 p)",
                line=dict(color="#6A4C93", width=2),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=acute.index, y=acute.values,
                mode="lines", name="Akuutne (7 p)",
                line=dict(color="#E55934", width=2, dash="dot"),
            )
        )
    fig.update_layout(
        title="Igapäevane treeningkoormus",
        xaxis_title="Kuupäev",
        yaxis_title="TRIMP",
        height=400,
        # Bottom margin holds the legend; top stays compact so the title sits
        # alone above the plot (previously the horizontal legend was anchored
        # at y=1.02 next to the title and overlapped it in narrow columns).
        margin=dict(l=40, r=20, t=60, b=90),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.22,
            xanchor="center", x=0.5,
        ),
    )
    return fig


def weekly_volume_chart(activities: list[TrainingActivity]) -> go.Figure:
    fig = go.Figure()
    if not activities:
        fig.update_layout(title="Nädalamaht (km)", height=300)
        return fig
    df = pd.DataFrame(
        [{"date": pd.Timestamp(a.activity_date), "km": a.distance_km} for a in activities]
    )
    df["week_start"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    weekly = df.groupby("week_start")["km"].sum().reset_index()
    fig.add_trace(
        go.Bar(
            x=weekly["week_start"], y=weekly["km"],
            marker_color="#4C956C",
            text=[f"{v:.0f}" for v in weekly["km"]],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="Nädala kogumaht (km)",
        xaxis_title="Nädala algus",
        yaxis_title="km",
        height=300,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def fitness_form_chart(daily_load: pd.Series) -> go.Figure:
    """Banister fitness/fatigue/form curves on a single timeline.

    Two y-axes: the left axis shows TRIMP (CTL and ATL), the right axis shows
    TSB centred on zero so positive form sits above the line and fatigue dips
    below. The race-ready band (TSB +5…+25) is shaded.
    """
    df = fitness_form(daily_load)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title="Fitness / Fatigue / Form (pole andmeid)", height=360)
        return fig

    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["ctl"],
            name="Fitness (CTL 42 p)",
            line=dict(color="#3A86FF", width=2.5),
            hovertemplate="%{x|%Y-%m-%d}: CTL %{y:.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["atl"],
            name="Fatigue (ATL 7 p)",
            line=dict(color="#E55934", width=1.8),
            hovertemplate="%{x|%Y-%m-%d}: ATL %{y:.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["tsb"],
            name="Form (TSB)",
            line=dict(color="#4C956C", width=1.8, dash="dot"),
            yaxis="y2",
            hovertemplate="%{x|%Y-%m-%d}: TSB %{y:+.0f}<extra></extra>",
        )
    )
    # Race-ready band on the secondary axis.
    fig.add_hrect(
        y0=5, y1=25,
        fillcolor="rgba(76, 149, 108, 0.10)", line_width=0, layer="below",
        yref="y2",
        annotation_text="Race-ready (TSB +5…+25)", annotation_position="top right",
    )
    # Zero line for TSB.
    fig.add_hline(y=0, line_color="#888", line_width=1, line_dash="dash", yref="y2")

    fig.update_layout(
        title="Fitness / Fatigue / Form (Banister)",
        xaxis_title="Kuupäev",
        yaxis=dict(title="CTL / ATL (TRIMP)", rangemode="tozero"),
        yaxis2=dict(title="TSB", overlaying="y", side="right", zeroline=False),
        height=380,
        margin=dict(l=40, r=60, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
    )
    return fig


def pb_progression_chart(
    activities: list[TrainingActivity],
    distance_label: str,
    nominal_km: float,
) -> go.Figure:
    """Scatter every attempt at the nominal distance + a step-function PB line."""
    df = progression_at_distance(activities, nominal_km)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(
            title=f"Tippajad {distance_label} (pole sobivaid jookse)",
            height=320,
            annotations=[dict(
                text=f"Ühegi jooksu pikkus pole {nominal_km} km lähedal (±5%).",
                showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
            )],
        )
        return fig

    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df["pace_min_per_km"],
            mode="markers", name="Üksikud katsed",
            marker=dict(size=7, color="#888888", opacity=0.55),
            customdata=df[["activity_notes"]],
            hovertemplate=(
                "%{x|%Y-%m-%d}: %{y:.2f} min/km"
                "<br>%{customdata[0]}<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df["pb_so_far_pace"],
            mode="lines", name="PB siiani",
            line=dict(color="#E55934", width=2.5, shape="hv"),
            hovertemplate="%{x|%Y-%m-%d}: PB %{y:.2f} min/km<extra></extra>",
        )
    )

    fig.update_layout(
        title=f"Tippaja progressioon — {distance_label}",
        xaxis_title="Kuupäev",
        yaxis=dict(title="Tempo (min/km)", autorange="reversed"),  # faster = lower = visually up
        height=380,
        margin=dict(l=40, r=20, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def rpe_trend_chart(
    activities: list[TrainingActivity],
    profile: AthleteProfile | None = None,
) -> go.Figure:
    """Plot logged RPE as markers and HR-derived RPE as a dashed line.

    Most athletes log RPE inconsistently, so the markers are sparse. Filling in
    the gaps with a Karvonen HRR-based estimate keeps the trend visible whenever
    HR is available — usually 100% on Polar/Garmin-equipped runners.
    """
    fig = go.Figure()
    if not activities:
        fig.update_layout(
            title="RPE trend (pole andmeid)", height=260,
            annotations=[dict(text="Andmeid pole — lae sisse trennid.", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)],
        )
        return fig

    sorted_acts = sorted(activities, key=lambda a: a.activity_date)
    logged = [(pd.Timestamp(a.activity_date), a.rpe) for a in sorted_acts if a.rpe is not None]
    estimated: list[tuple[pd.Timestamp, int]] = []
    if profile is not None:
        for a in sorted_acts:
            est = estimate_rpe_from_hr(a.avg_hr, profile.resting_hr, profile.max_hr)
            if est is not None:
                estimated.append((pd.Timestamp(a.activity_date), est))

    if not logged and not estimated:
        fig.update_layout(
            title="RPE trend (pole andmeid)", height=260,
            annotations=[dict(text="Logi RPE või lisa pulsiandmed, et trend ilmuks.", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)],
        )
        return fig

    if estimated:
        edf = pd.DataFrame(estimated, columns=["date", "rpe"])
        fig.add_trace(
            go.Scatter(
                x=edf["date"], y=edf["rpe"],
                mode="lines+markers", name="Hinnatud (HRR)",
                line=dict(color="#3A86FF", width=2, dash="dot"),
                marker=dict(size=4, color="#3A86FF"),
                opacity=0.75,
                hovertemplate="%{x|%Y-%m-%d}: hinnatud RPE %{y}<extra></extra>",
            )
        )
    if logged:
        ldf = pd.DataFrame(logged, columns=["date", "rpe"])
        fig.add_trace(
            go.Scatter(
                x=ldf["date"], y=ldf["rpe"],
                mode="markers", name="Logitud (sportlane)",
                marker=dict(size=10, color="#FFB400", line=dict(color="#222", width=1)),
                hovertemplate="%{x|%Y-%m-%d}: logitud RPE %{y}<extra></extra>",
            )
        )

    title = "Subjektiivne raskushinnang (RPE 1–10)"
    if estimated and not logged:
        title += " — sünteesitud pulsireservist (Karvonen HRR×10)"
    fig.update_layout(
        title=title,
        xaxis_title="Kuupäev",
        yaxis_title="RPE",
        yaxis=dict(range=[0, 10]),
        height=260,
        margin=dict(l=40, r=20, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
