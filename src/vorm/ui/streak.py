"""Streak counter + GitHub-style heatmap for the §4.3 daily-log discipline.

A consecutive-day streak is the simplest "did you actually use the tool"
metric — it's what makes the §4.3 evaluation column meaningful. The heatmap
gives the same information for a longer window so the user can see whether
they're trending up or have a recent gap.
"""

from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go

from ..data.storage import DailyLogEntry


def streak_count(logs: list[DailyLogEntry], today: date) -> int:
    """Days logged ending at ``today`` (with a one-day grace if today is empty).

    The grace handles the realistic case where the user opens the app before
    they've logged today — without it, the streak would read 0 every morning.
    """
    if not logs:
        return 0
    log_dates = {entry.log_date for entry in logs}
    cursor = today
    if cursor not in log_dates:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor in log_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak(logs: list[DailyLogEntry]) -> int:
    """Longest consecutive-day run anywhere in history. Independent of today."""
    if not logs:
        return 0
    log_dates = sorted({entry.log_date for entry in logs})
    best = current = 1
    for prev, curr in zip(log_dates, log_dates[1:], strict=False):
        if (curr - prev).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def log_heatmap_chart(
    logs: list[DailyLogEntry], today: date, weeks: int = 12,
) -> go.Figure:
    """7-row × N-column heatmap of which days the user logged.

    Rows are weekdays (Mon top → Sun bottom), columns are calendar weeks. Each
    cell carries the ``usefulness`` score (1–5) when present, else 0.5 to mark
    "logged but no rating", and 0 for empty days. The cell hover shows the
    actual date so the user can correlate dips with real life.
    """
    log_index = {entry.log_date: entry for entry in logs}
    weekday_labels = ["E", "T", "K", "N", "R", "L", "P"]

    start = today - timedelta(days=weeks * 7 - 1)
    start -= timedelta(days=start.weekday())  # snap to Monday

    z: list[list[float]] = [[] for _ in range(7)]
    text: list[list[str]] = [[] for _ in range(7)]
    column_dates: list[date] = []
    cursor = start
    while cursor <= today:
        column_dates.append(cursor)
        for offset in range(7):
            day = cursor + timedelta(days=offset)
            entry = log_index.get(day) if day <= today else None
            if entry is None:
                z[offset].append(0.0)
                text[offset].append(
                    f"{day.isoformat()}<br>—" if day <= today else ""
                )
            else:
                z[offset].append(float(entry.usefulness or 0.5) or 0.5)
                text[offset].append(
                    f"{day.isoformat()}<br>kasulikkus: "
                    f"{entry.usefulness if entry.usefulness else '—'}"
                )
        cursor += timedelta(days=7)

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=[d.isoformat() for d in column_dates],
            y=weekday_labels,
            text=text,
            hovertemplate="%{text}<extra></extra>",
            colorscale=[
                [0.00, "#2d333b"],   # not logged (dark)
                [0.01, "#9be9a8"],   # logged
                [0.25, "#40c463"],
                [0.50, "#30a14e"],
                [0.75, "#216e39"],
                [1.00, "#0a3d20"],   # high usefulness
            ],
            showscale=False,
            xgap=2,
            ygap=2,
        )
    )
    fig.update_layout(
        title=f"Viimase {weeks} nädala logimisaktiivsus",
        height=240,
        margin=dict(l=40, r=20, t=50, b=30),
        yaxis=dict(autorange="reversed"),  # Mon on top
        xaxis=dict(showgrid=False),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig
