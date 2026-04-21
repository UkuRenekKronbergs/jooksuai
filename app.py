"""Streamlit entry point for jooksuai.

Run with: ``streamlit run app.py``

UI layout:
- Sidebar: data source, athlete profile, analysis date
- Tab 1 (Tänane soovitus): today's input form → recommendation
- Tab 2 (Koormuse ajalugu): ACWR + daily load + weekly volume + RPE trend
- Tab 3 (Retrospektiivne test): pick a past date, see what the model would advise
"""

from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.runtime as st_runtime

from jooksuai.config import load_config
from jooksuai.data import (
    AthleteProfile,
    DailySubjective,
    TrainingActivity,
    generate_sample_activities,
    load_sample_profile,
)
from jooksuai.data.csv_loader import load_activities_csv
from jooksuai.data.strava import StravaNotConfigured, fetch_recent_activities
from jooksuai.llm import LLMNotAvailable, build_prompt, generate_recommendation
from jooksuai.metrics import build_load_timeseries, summarize_load
from jooksuai.rules import evaluate_safety_rules
from jooksuai.ui import acwr_chart, daily_load_chart, rpe_trend_chart, weekly_volume_chart

if __name__ == "__main__" and not st_runtime.exists():
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]
    raise SystemExit(stcli.main())

st.set_page_config(page_title="jooksuai — treeningkoormuse analüüsija", page_icon="🏃", layout="wide")


def _get_activities(source: str, uploaded_csv, days: int, cfg) -> list[TrainingActivity]:
    if source == "Näidisandmed":
        return generate_sample_activities(days=days)
    if source == "CSV-fail":
        if uploaded_csv is None:
            return []
        try:
            return load_activities_csv(io.StringIO(uploaded_csv.getvalue().decode("utf-8")))
        except Exception as exc:
            st.error(f"CSV-i lugemine ebaõnnestus: {exc}")
            return []
    if source == "Strava API":
        try:
            return fetch_recent_activities(
                client_id=cfg.strava_client_id,
                client_secret=cfg.strava_client_secret,
                refresh_token=cfg.strava_refresh_token,
                days=days,
            )
        except StravaNotConfigured as exc:
            st.error(str(exc))
            return []
        except Exception as exc:
            st.error(f"Strava päring ebaõnnestus: {exc}")
            return []
    return []


def _render_profile_editor(default: AthleteProfile) -> AthleteProfile:
    with st.expander("Sportlase profiil", expanded=False):
        name = st.text_input("Nimi", value=default.name)
        col1, col2, col3 = st.columns(3)
        age = col1.number_input("Vanus", min_value=12, max_value=80, value=default.age)
        sex = col2.selectbox("Sugu", ["M", "F"], index=0 if default.sex == "M" else 1)
        years = col3.number_input("Treeningstaaž (a)", min_value=0, max_value=50, value=default.training_years)
        col4, col5 = st.columns(2)
        max_hr = col4.number_input("Maksimaalne pulss", min_value=120, max_value=230, value=default.max_hr)
        rest_hr = col5.number_input("Puhkepulss", min_value=30, max_value=90, value=default.resting_hr)
        goal = st.text_input("Hooaja eesmärk", value=default.season_goal)

        st.markdown("**Tippajad** (formaadis `M:SS` või `MM:SS`)")
        pb_cols = st.columns(4)
        pbs: dict[str, str] = {}
        for col, key, label in zip(
            pb_cols,
            ("1500m", "3000m", "5000m", "10000m"),
            ("1500 m", "3000 m", "5000 m", "10 000 m"),
            strict=False,
        ):
            v = col.text_input(label, value=default.personal_bests.get(key, ""), key=f"pb_{key}")
            if v.strip():
                pbs[key] = v.strip()

        st.markdown("**Künnis-tempo** (jäta 0, et tuletada 10 km PB-st automaatselt)")
        threshold_input = st.number_input(
            "min/km",
            min_value=0.0,
            max_value=10.0,
            value=default.threshold_pace_min_per_km or 0.0,
            step=0.05,
            format="%.2f",
            help="HR-andmete puudumisel kasutatakse seda intensity factor'i arvutamiseks (rTSS-stiilis fallback).",
        )
        threshold_value = threshold_input if threshold_input > 0 else None

    return AthleteProfile(
        name=name,
        age=int(age),
        sex=sex,
        max_hr=int(max_hr),
        resting_hr=int(rest_hr),
        training_years=int(years),
        season_goal=goal,
        personal_bests=pbs or default.personal_bests,
        threshold_pace_min_per_km=threshold_value,
    )


def _recommendation_color(category: str) -> str:
    return {
        "Jätka plaanipäraselt": "#4C956C",
        "Vähenda intensiivsust": "#FFB400",
        "Alternatiivne treening": "#3A86FF",
        "Lisa taastumispäev": "#E55934",
    }.get(category, "#888888")


def _render_verdict_box(verdict, llm_result=None):
    category = llm_result.category if llm_result else verdict.recommendation.value
    color = _recommendation_color(category)
    source_badge = "LLM" if llm_result else "reegel"
    st.markdown(
        f"""
        <div style="border-left: 6px solid {color}; padding: 14px 18px; background: #FAFAFA; border-radius: 4px;">
          <div style="font-size: 12px; color: #666; letter-spacing: 0.5px; text-transform: uppercase;">
            Soovitus — allikas: {source_badge}
          </div>
          <div style="font-size: 22px; font-weight: 600; color: {color}; margin: 4px 0 10px;">
            {category}
          </div>
          <div style="font-size: 15px; line-height: 1.45; color: #222;">
            {(llm_result.rationale if llm_result else "Reeglipõhine soovitus — lisa API võti detailse põhjenduse saamiseks.")}
          </div>
          {"<div style='margin-top: 10px; font-size: 14px; color: #444;'><b>Asendus:</b> " + llm_result.modification + "</div>" if (llm_result and llm_result.modification) else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if llm_result:
        meta = f"Mudel: `{llm_result.model}` · prompt v{llm_result.prompt_version} · kindlus: {llm_result.confidence}"
        if llm_result.input_tokens:
            meta += f" · input tokens: {llm_result.input_tokens} · output tokens: {llm_result.output_tokens}"
        st.caption(meta)


def _render_safety_flags(verdict):
    if not verdict.flags:
        st.success("Ohutusreeglid: kõik korras — kriitilisi ega hoiatusi pole.")
        return
    for f in verdict.flags:
        if f.severity.value == "critical":
            st.error(f"⚠ **{f.code}** — {f.message}")
        else:
            st.warning(f"**{f.code}** — {f.message}")


def _summary_table(activities: list[TrainingActivity], today: date) -> pd.DataFrame:
    cutoff = today - timedelta(days=13)
    rows = [
        {
            "Kuupäev": a.activity_date.isoformat(),
            "Tüüp": a.notes or a.activity_type,
            "km": round(a.distance_km, 1),
            "min": round(a.duration_min, 0),
            "avg HR": str(a.avg_hr) if a.avg_hr else "—",
            "RPE": str(a.rpe) if a.rpe is not None else "—",
        }
        for a in sorted(activities, key=lambda x: x.activity_date)
        if cutoff <= a.activity_date <= today
    ]
    return pd.DataFrame(rows)


# --- Sidebar --------------------------------------------------------------
cfg = load_config()

st.sidebar.title("🏃 jooksuai")
st.sidebar.caption("AI-põhine treeningkoormuse analüüsija")

data_source_options = ["Näidisandmed", "CSV-fail"]
if cfg.has_strava:
    data_source_options.append("Strava API")

source = st.sidebar.radio("Andmeallikas", data_source_options)
days = st.sidebar.slider("Päevade arv", min_value=28, max_value=180, value=90, step=7)

uploaded_csv = None
if source == "CSV-fail":
    uploaded_csv = st.sidebar.file_uploader(
        "Lae Strava-eksport või natiivformaadis CSV",
        type=["csv"],
        help="Natiivne formaat: id, activity_date, activity_type, distance_km, duration_min, avg_hr, rpe, notes. "
        "Strava-eksport (Activity Date, Distance jne) töötab samuti.",
    )

st.sidebar.divider()

profile = _render_profile_editor(load_sample_profile())

st.sidebar.divider()
analysis_date: date = st.sidebar.date_input("Analüüsi kuupäev", value=date.today())

st.sidebar.divider()
if cfg.has_llm:
    st.sidebar.success(f"LLM aktiivne: {cfg.llm_provider} / {cfg.llm_model}")
else:
    st.sidebar.info("LLM pole seadistatud — reeglivastus kuvatakse. Lisa ANTHROPIC_API_KEY .env-i täieliku analüüsi jaoks.")

# --- Main -----------------------------------------------------------------

st.title("Treeningkoormuse analüüs")
st.caption(
    "Tööriist on otsustustugi, mitte asendaja treenerile ega arstile. "
    "Vigastuse või haiguse kahtluse korral pöördu spetsialisti poole."
)

activities = _get_activities(source, uploaded_csv, days, cfg)

if not activities:
    st.info("Andmed pole veel laaditud. Vali vasakul andmeallikas või lae CSV.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["Tänane soovitus", "Koormuse ajalugu", "Retrospektiivne test"])

# --- Tab 1: today's recommendation
with tab1:
    st.subheader(f"Analüüsi seisuga {analysis_date.isoformat()}")

    summary = summarize_load(activities, profile, as_of=analysis_date)

    col_metrics = st.columns(4)
    col_metrics[0].metric(
        "ACWR",
        f"{summary.acwr:.2f}" if summary.acwr is not None else "—",
        help="Acute 7 p / Chronic 28 p keskmine koormus. Sweet-spot 0.8–1.3.",
    )
    col_metrics[1].metric("7 p TRIMP", f"{summary.acute_7d:.0f}")
    col_metrics[2].metric("28 p TRIMP", f"{summary.chronic_28d:.0f}")
    col_metrics[3].metric(
        "Monotoonsus",
        f"{summary.monotony:.2f}" if summary.monotony is not None else "—",
        help="Foster monotony = 7-päeva mean / std. ≥ 2.0 = vähe varieeruvust.",
    )

    hr_coverage = sum(1 for a in activities if a.avg_hr) / max(len(activities), 1)
    if hr_coverage < 0.5:
        threshold = profile.effective_threshold_pace
        if threshold:
            st.info(
                f"Pulsiandmeid ainult {hr_coverage * 100:.0f}%-l treeningutest. "
                f"Kasutan tempo-põhist fallback'i (rTSS-stiilis), threshold-tempo **{threshold:.2f} min/km**. "
                f"Sea profiilis täpselt või lisa 10 km PB."
            )
        else:
            st.warning(
                f"Pulsiandmeid ainult {hr_coverage * 100:.0f}%-l treeningutest ja künnis-tempo pole määratud. "
                f"Koormus arvutub 0-ks. Lisa profiili threshold-tempo või 10 km PB."
            )

    st.divider()

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("#### Tänane plaan + subjektiivsed sisendid")
        today_plan = st.text_area(
            "Täna planeeritud treening",
            placeholder="Nt: 8×400 m 75 s pauside vahel; või 45 min kerge aeroobne.",
            height=100,
        )
        colA, colB = st.columns(2)
        rpe_yesterday = colA.slider("Eilse treeningu RPE (1–10)", min_value=1, max_value=10, value=5)
        sleep_hours = colB.number_input("Uneaeg (h)", min_value=0.0, max_value=14.0, value=7.5, step=0.5)
        colC, colD = st.columns(2)
        stress = colC.slider("Stressitase (1–5)", min_value=1, max_value=5, value=2)
        illness = colD.checkbox("Haigus / tundsin end halvasti")
        notes = st.text_input("Märkmed (valikuline)", placeholder="Nt: kerge köha, jalg pinges...")

    with c2:
        st.markdown("#### Ohutusreeglid")
        subjective = DailySubjective(
            entry_date=analysis_date,
            rpe_yesterday=rpe_yesterday,
            sleep_hours=sleep_hours,
            stress_level=stress,
            illness=illness,
            notes=notes or None,
        )
        verdict = evaluate_safety_rules(summary, subjective)
        _render_safety_flags(verdict)

    st.divider()

    if st.button("Hinda koormust", type="primary", width="stretch"):
        prompt_bundle = build_prompt(
            profile=profile,
            activities=activities,
            summary=summary,
            verdict=verdict,
            today_plan=today_plan,
            subjective=subjective,
            today=analysis_date,
        )

        llm_result = None
        if cfg.has_llm and today_plan.strip():
            with st.spinner("Küsin LLM-lt soovitust..."):
                try:
                    llm_result = generate_recommendation(prompt_bundle, cfg)
                except LLMNotAvailable as exc:
                    st.warning(f"LLM kättesaamatu: {exc}. Näitan reeglipõhist vastust.")
                except Exception as exc:
                    st.error(f"LLM viga: {exc}")
        elif not today_plan.strip():
            st.warning("Sisesta täna planeeritud treening, et saada LLM-soovitust.")

        _render_verdict_box(verdict, llm_result)

        with st.expander("Näita LLM-i kasutatud prompti (diagnostika)"):
            st.code(prompt_bundle.user, language="markdown")

# --- Tab 2: history
with tab2:
    daily = build_load_timeseries(activities, profile, end=analysis_date)
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(acwr_chart(daily), width="stretch")
    with col2:
        st.plotly_chart(daily_load_chart(daily), width="stretch")

    col3, col4 = st.columns(2)
    with col3:
        st.plotly_chart(weekly_volume_chart(activities), width="stretch")
    with col4:
        st.plotly_chart(rpe_trend_chart(activities), width="stretch")

    st.markdown("#### Viimase 14 päeva kokkuvõte")
    st.dataframe(_summary_table(activities, analysis_date), width="stretch", hide_index=True)

# --- Tab 3: retrospective test
with tab3:
    st.markdown(
        "Retrospektiivne test: vali varasem kuupäev, mille puhul näed mudeli soovitust, "
        "arvestades ainult *selleks päevaks* teadaolevaid andmeid. Järgi plaani "
        "(10 varasemat päeva) — võrdle oma tegeliku otsusega."
    )

    earliest = min(a.activity_date for a in activities)
    latest = max(a.activity_date for a in activities)
    if latest <= earliest + timedelta(days=30):
        st.warning("Liiga lühike andmeloend retrospektiivseks testiks (vaja ≥ 30 päeva).")
    else:
        retro_date = st.date_input(
            "Retrospektiivne kuupäev",
            value=latest - timedelta(days=7),
            min_value=earliest + timedelta(days=28),
            max_value=latest,
            key="retro_date",
        )
        retro_plan = st.text_input(
            "Mida sa sel päeval tegid (või pidid tegema)?",
            placeholder="Nt: 5x1000m @ 3:25",
            key="retro_plan",
        )
        retro_rpe = st.slider("Eilse treeningu RPE", 1, 10, 5, key="retro_rpe")

        if st.button("Käivita retrospektiivne hindamine"):
            past_activities = [a for a in activities if a.activity_date <= retro_date]
            retro_summary = summarize_load(past_activities, profile, as_of=retro_date)
            retro_subjective = DailySubjective(
                entry_date=retro_date,
                rpe_yesterday=retro_rpe,
            )
            retro_verdict = evaluate_safety_rules(retro_summary, retro_subjective)

            st.markdown(f"#### Seis {retro_date.isoformat()} kohta")
            colA, colB, colC = st.columns(3)
            colA.metric("ACWR", f"{retro_summary.acwr:.2f}" if retro_summary.acwr else "—")
            colB.metric("7 p TRIMP", f"{retro_summary.acute_7d:.0f}")
            colC.metric("28 p TRIMP", f"{retro_summary.chronic_28d:.0f}")

            retro_llm = None
            if cfg.has_llm and retro_plan.strip():
                retro_prompt = build_prompt(
                    profile=profile,
                    activities=past_activities,
                    summary=retro_summary,
                    verdict=retro_verdict,
                    today_plan=retro_plan,
                    subjective=retro_subjective,
                    today=retro_date,
                )
                with st.spinner("LLM retrospektiivne hinnang..."):
                    try:
                        retro_llm = generate_recommendation(retro_prompt, cfg)
                    except Exception as exc:
                        st.error(f"LLM viga: {exc}")

            _render_safety_flags(retro_verdict)
            _render_verdict_box(retro_verdict, retro_llm)
