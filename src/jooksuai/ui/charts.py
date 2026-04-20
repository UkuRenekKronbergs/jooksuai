"""Plotly charts for the Streamlit UI.

All functions return a `go.Figure`. The Streamlit app is responsible for
calling `st.plotly_chart` so these are easy to reuse from a notebook.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ..data.models import TrainingActivity
from ..metrics.load import (
    ACWR_DANGER_HIGH,
    ACWR_DANGER_LOW,
    ACWR_SWEET_SPOT,
    acwr_series,
)

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
        height=360,
        margin=dict(l=40, r=20, t=60, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
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


def rpe_trend_chart(activities: list[TrainingActivity]) -> go.Figure:
    fig = go.Figure()
    rpe_rows = [
        {"date": pd.Timestamp(a.activity_date), "rpe": a.rpe}
        for a in activities
        if a.rpe is not None
    ]
    if not rpe_rows:
        fig.update_layout(
            title="RPE trend (pole andmeid)", height=260,
            annotations=[dict(text="Logi RPE igapäeval, et trend ilmuks.", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)],
        )
        return fig
    df = pd.DataFrame(rpe_rows).sort_values("date")
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df["rpe"],
            mode="lines+markers", name="RPE",
            line=dict(color="#FFB400", width=2),
            marker=dict(size=7),
        )
    )
    fig.update_layout(
        title="Subjektiivne raskushinnang (RPE 1–10)",
        xaxis_title="Kuupäev",
        yaxis_title="RPE",
        yaxis=dict(range=[0, 10]),
        height=260,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig
