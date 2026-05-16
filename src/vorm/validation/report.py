"""PDF generator for the Project Plan §4 validation report.

Bundles three sections into one downloadable A4 document:

1. Athlete profile snapshot (so the reader knows whose data this is)
2. §4.3 daily-log summary (n days, mean usefulness/persuasiveness, follow-rate)
3. §4.2 coach blind-comparison results (match/close/wrong breakdown + per-day
   table)

reportlab's default Helvetica supports Estonian diacritics (õ ä ö ü) via
WinAnsi, so no custom font wiring is needed for course-project deliverables.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..data.models import AthleteProfile
from ..data.storage import CoachDecision, DailyLogEntry

_CAREFUL = {
    "Vähenda intensiivsust",
    "Alternatiivne treening",
    "Lisa taastumispäev",
}


def _agreement(model: str, coach: str) -> str:
    """Same three-bucket scheme as the UI: match / close / wrong."""
    if model == coach:
        return "match"
    if model in _CAREFUL and coach in _CAREFUL:
        return "close"
    return "wrong"


def _profile_table(profile: AthleteProfile) -> Table:
    rows = [
        ["Nimi", profile.name],
        ["Vanus", str(profile.age)],
        ["Sugu", profile.sex],
        ["Treeningstaaž (a)", str(profile.training_years)],
        ["Maksimaalne pulss", str(profile.max_hr)],
        ["Puhkepulss", str(profile.resting_hr)],
        ["Hooaja eesmärk", profile.season_goal or "—"],
    ]
    table = Table(rows, colWidths=[5 * cm, 10 * cm])
    table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return table


def _daily_log_summary_table(daily_logs: list[DailyLogEntry]) -> Table:
    useful = [e.usefulness for e in daily_logs if e.usefulness]
    pers = [e.persuasiveness for e in daily_logs if e.persuasiveness]
    feel = [e.next_session_feeling for e in daily_logs if e.next_session_feeling]
    followed_yes = sum(1 for e in daily_logs if e.followed == "yes")
    followed_partial = sum(1 for e in daily_logs if e.followed == "partial")
    followed_no = sum(1 for e in daily_logs if e.followed == "no")
    n = len(daily_logs)

    def fmt_mean(xs: list[int]) -> str:
        return f"{sum(xs) / len(xs):.2f}" if xs else "—"

    rows = [
        ["Näitaja", "Väärtus"],
        ["Logitud päevi", str(n)],
        ["Keskmine kasulikkus (1–5)", fmt_mean(useful)],
        ["Keskmine veenvus (1–5)", fmt_mean(pers)],
        ["Keskmine järgmise treeningu enesetunne (1–5)", fmt_mean(feel)],
        ["Järgis soovitust 'jah'", f"{followed_yes} / {n}" if n else "—"],
        ["Järgis soovitust 'osaliselt'", f"{followed_partial} / {n}" if n else "—"],
        ["Järgis soovitust 'ei'", f"{followed_no} / {n}" if n else "—"],
    ]
    table = Table(rows, colWidths=[10 * cm, 5 * cm], repeatRows=1)
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dee5ec")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return table


def _coach_comparison_tables(
    coach_decisions: list[CoachDecision],
    daily_logs: list[DailyLogEntry],
) -> tuple[Table | None, Table | None]:
    log_by_date = {e.log_date: e for e in daily_logs}
    pairs = [
        (d, log_by_date[d.decision_date])
        for d in coach_decisions
        if d.decision_date in log_by_date
    ]
    if not pairs:
        return None, None

    buckets = [
        _agreement(log.recommended_category, d.recommended_category)
        for d, log in pairs
    ]
    n = len(pairs)
    match = buckets.count("match")
    close = buckets.count("close")
    wrong = buckets.count("wrong")

    def pct(x: int) -> str:
        return f"{x} ({x / n * 100:.0f}%)" if n else "—"

    summary_rows = [
        ["Näitaja", "Väärtus"],
        ["Võrreldud päevi", str(n)],
        ["Match (täpne kategooria)", pct(match)],
        ["Close (mõlemad ettevaatlikud)", pct(close)],
        ["Wrong (stance-vastuolu)", pct(wrong)],
    ]
    summary = Table(summary_rows, colWidths=[10 * cm, 5 * cm], repeatRows=1)
    summary.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dee5ec")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ])
    )

    rows = [["Kuupäev", "Mudel", "Treener", "Kattuvus"]]
    for d, log in sorted(pairs, key=lambda p: p[0].decision_date):
        rows.append([
            d.decision_date.isoformat(),
            log.recommended_category,
            d.recommended_category,
            _agreement(log.recommended_category, d.recommended_category),
        ])
    breakdown = Table(
        rows, colWidths=[3 * cm, 5 * cm, 5 * cm, 2.5 * cm], repeatRows=1,
    )
    breakdown.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dee5ec")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    return summary, breakdown


def build_validation_report(
    profile: AthleteProfile,
    daily_logs: list[DailyLogEntry],
    coach_decisions: list[CoachDecision],
    *,
    report_date: date | None = None,
) -> bytes:
    """Render the validation PDF and return it as raw bytes (for download)."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Vorm.ai valideerimisaruanne — {profile.name}",
    )
    styles = getSampleStyleSheet()
    story: list = []

    story.append(Paragraph("Vorm.ai — valideerimisaruanne", styles["Title"]))
    story.append(Paragraph(
        f"Aruande kuupäev: <b>{(report_date or date.today()).isoformat()}</b>",
        styles["Normal"],
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Sportlase profiil", styles["Heading2"]))
    story.append(_profile_table(profile))
    story.append(Spacer(1, 16))

    story.append(Paragraph("§4.3 Igapäevane kasutuslog", styles["Heading2"]))
    if not daily_logs:
        story.append(Paragraph(
            "Logikirjeid pole — käivita rakendus ja vajuta päevalogi all 'Salvesta'.",
            styles["Normal"],
        ))
    else:
        story.append(_daily_log_summary_table(daily_logs))
    story.append(Spacer(1, 16))

    story.append(Paragraph("§4.2 Treeneri pimemenetluse võrdlus", styles["Heading2"]))
    summary_table, breakdown_table = _coach_comparison_tables(
        coach_decisions, daily_logs,
    )
    if summary_table is None:
        story.append(Paragraph(
            "Treeneri otsuste ja päevalogi ühisosa puudub — võrdluse ainestik on tühi.",
            styles["Normal"],
        ))
    else:
        story.append(summary_table)
        story.append(Spacer(1, 10))
        story.append(Paragraph("Päevade lõikes:", styles["Normal"]))
        story.append(Spacer(1, 4))
        story.append(breakdown_table)

    doc.build(story)
    return buf.getvalue()
