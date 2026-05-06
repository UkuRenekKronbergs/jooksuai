"""Streamlit entry point for Vorm.ai.

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

from vorm.config import load_config
from vorm.data import (
    AthleteProfile,
    DailySubjective,
    TrainingActivity,
    generate_sample_activities,
    load_sample_profile,
)
from vorm.data.csv_loader import load_activities_csv
from vorm.data.strava import StravaNotConfigured, fetch_recent_activities
from vorm.llm import LLMNotAvailable, build_prompt, generate_recommendation
from vorm.metrics import (
    PB_DISTANCES,
    build_load_timeseries,
    find_personal_bests,
    summarize_load,
)
from vorm.planning import PlanGenerationError, PlanGoal, generate_training_plan
from vorm.rules import evaluate_safety_rules
from vorm.ui import (
    acwr_chart,
    daily_load_chart,
    fitness_form_chart,
    pb_progression_chart,
    rpe_trend_chart,
    weekly_volume_chart,
)

if __name__ == "__main__" and not st_runtime.exists():
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]
    raise SystemExit(stcli.main())

st.set_page_config(page_title="Vorm.ai — treeningkoormuse analüüsija", page_icon="🏃", layout="wide")


def _get_activities(source: str, uploaded_csv, days: int, cfg) -> list[TrainingActivity]:
    if source == "Näidisandmed":
        return generate_sample_activities(days=days)
    if source == "CSV-fail":
        if uploaded_csv is None:
            return []
        try:
            # utf-8-sig strips a BOM if present — Excel-edited Strava exports
            # often have one, vanilla Strava exports don't. Either works.
            return load_activities_csv(io.StringIO(uploaded_csv.getvalue().decode("utf-8-sig")))
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


_WEEKDAY_ET = ["Esmaspäev", "Teisipäev", "Kolmapäev", "Neljapäev", "Reede", "Laupäev", "Pühapäev"]
_PHASE_ET = {
    "base": "Baas",
    "build": "Arendus",
    "peak": "Tipp",
    "taper": "Mahalaadimine",
    "race": "Võistlus",
}


def _format_pace(pace_min_per_km: float | None) -> str:
    """Decimal min/km → `M:SS/km`. Empty for missing/zero."""
    if not pace_min_per_km or pace_min_per_km <= 0:
        return ""
    minutes = int(pace_min_per_km)
    seconds = round((pace_min_per_km - minutes) * 60)
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/km"


def _build_plan_csv(plan) -> bytes:
    """Build a human-friendly UTF-8-with-BOM CSV from a TrainingPlan.

    BOM lets Excel autodetect UTF-8 (so `ä, ö, ü, õ` don't turn into `Ã¤`).
    Headers, weekday, and phase are translated to Estonian; pace is formatted
    as M:SS/km because that's how runners actually read it.
    """
    rows = []
    for w in plan.weeks:
        phase = _PHASE_ET.get(w.phase.lower(), w.phase)
        for s in w.sessions:
            rows.append({
                "Nädal": w.week_number,
                "Faas": phase,
                "Kuupäev": s.session_date.isoformat(),
                "Nädalapäev": _WEEKDAY_ET[s.session_date.weekday()],
                "Trenni tüüp": s.session_type,
                "Intensiivsus": s.intensity_zone,
                "Kestus (min)": int(round(s.duration_min)) if s.duration_min else 0,
                "Distants (km)": round(s.distance_km, 1) if s.distance_km else "",
                "Sihttempo": _format_pace(s.target_pace_min_per_km),
                "Kirjeldus": (s.description or "").replace("\n", " ").strip(),
                "Nädala maht (km)": round(w.target_volume_km, 1),
                "Nädala märkmed": (w.notes or "").replace("\n", " ").strip(),
            })
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


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

st.sidebar.title("🏃 Vorm.ai")
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

# Load activities before the date picker so we can default the date to the
# latest activity. Otherwise picking today() on a stale dataset shows
# ACWR=0/TRIMP=0 because the 7-day window is empty.
activities = _get_activities(source, uploaded_csv, days, cfg)
latest_activity_date = max((a.activity_date for a in activities), default=None)

# Reset the widget's session_state when (a) it has never been set, or (b) the
# stored choice is now past the loaded dataset — the latter handles the case
# where the user switches from sample data (latest = today) to a stale CSV
# export (latest = weeks ago) and the cached date is out of range.
default_date = latest_activity_date or date.today()
stored = st.session_state.get("analysis_date")
if stored is None or (latest_activity_date and stored > latest_activity_date):
    st.session_state["analysis_date"] = default_date

st.sidebar.divider()
analysis_date: date = st.sidebar.date_input(
    "Analüüsi kuupäev",
    key="analysis_date",
    help="Vaikimisi viimase laaditud trenni kuupäev. Muuda, et analüüsida mõnd varasemat hetke.",
)

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

if not activities:
    st.info("Andmed pole veel laaditud. Vali vasakul andmeallikas või lae CSV.")
    st.stop()

if analysis_date > latest_activity_date:
    gap_days = (analysis_date - latest_activity_date).days
    st.warning(
        f"Valitud kuupäev on {gap_days} päeva pärast viimast trenni "
        f"({latest_activity_date.isoformat()}). 7-päeva aknas ei pruugi olla andmeid — "
        "ACWR ja akuutne TRIMP võivad näidata 0. Liiguta kuupäev tagasi viimasele trenni-päevale."
    )

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Tänane soovitus", "Koormuse ajalugu", "Tippajad", "Retrospektiivne test", "Treeningkava"]
)

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
        st.plotly_chart(rpe_trend_chart(activities, profile), width="stretch")

    st.markdown("#### Fitness / Fatigue / Form (Banister)")
    st.caption(
        "**CTL** (sinine) on krooniline koormus — vorm. **ATL** (punane) on akuutne koormus — väsimus. "
        "**TSB** (roheline, paremal teljel) = CTL − ATL = vorm. Negatiivne TSB = väsinud, "
        "TSB +5…+25 = võistluseks valmis (rohelisel ribal)."
    )
    st.plotly_chart(fitness_form_chart(daily), width="stretch")

    st.markdown("#### Viimase 14 päeva kokkuvõte")
    st.dataframe(_summary_table(activities, analysis_date), width="stretch", hide_index=True)

# --- Tab 3: personal bests
with tab3:
    st.markdown(
        "Tippajad on tuvastatud iga jooksu põhjal, mille distants on standardse võistlus-distantsi "
        "lähedal (±5%). See püüab nii võistlused kui tempo-trennid, mille pikkus juhtus täpselt "
        "5/10 km olema — kummalgi juhul on jooksja praeguse vormi-lae markerina kasutatav."
    )

    pbs = find_personal_bests(activities)
    if not pbs:
        st.info("Ühtegi standard-distantsi PB-d ei tuvastatud. Lae rohkem andmeid.")
    else:
        rows = [
            {
                "Distants": p.distance_label,
                "Aeg": p.time_formatted,
                "Tempo": f"{p.pace_min_per_km:.2f} min/km",
                "Tegelik km": f"{p.activity_distance_km:.2f}",
                "Kuupäev": p.activity_date.isoformat(),
                "Kommentaar": (p.activity_notes or "")[:60],
            }
            for p in pbs
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        st.markdown("#### Progressioon ajas")
        labels_with_data = [(label, km) for label, km in PB_DISTANCES if any(p.distance_label == label for p in pbs)]
        if labels_with_data:
            chosen = st.selectbox(
                "Vali distants",
                options=[label for label, _ in labels_with_data],
                index=0,
                key="pb_distance",
            )
            chosen_km = next(km for label, km in labels_with_data if label == chosen)
            st.plotly_chart(pb_progression_chart(activities, chosen, chosen_km), width="stretch")

# --- Tab 4: retrospective test
with tab4:
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

# --- Tab 5: training plan generator
with tab5:
    st.markdown(
        "Genereeri **täielik päev-haaval treeningkava** võistluseks. Mudel arvestab sinu profiili, "
        "tippaegu, praegust vormi (ACWR/monotoonsus) ja valitud sihtaega. Kulu ~€0.001–0.01 per plaan "
        "sõltuvalt mudelist ja nädalate arvust."
    )

    if not cfg.has_llm:
        st.warning("LLM pole seadistatud — plaani genereerimine vajab LLM-i. Lisa API võti .env-i.")
    else:
        col1, col2, col3 = st.columns(3)
        event_name = col1.text_input("Võistluse nimi", value="Tallinna 10 km", key="plan_event")
        distance_km = col2.number_input(
            "Distants (km)", min_value=1.0, max_value=100.0, value=10.0, step=0.1, key="plan_distance"
        )
        event_date = col3.date_input(
            "Võistluse kuupäev",
            value=analysis_date + timedelta(weeks=10),
            min_value=analysis_date + timedelta(weeks=1),
            key="plan_event_date",
        )

        col4, col5 = st.columns(2)
        target_min = col4.number_input(
            "Sihtaeg (minutid)",
            min_value=3.0, max_value=360.0, value=35.0, step=0.25,
            help="Kokku minutites. Nt 34:40 = 34.67.",
            key="plan_target_min",
        )
        plan_start_date = col5.date_input(
            "Plaani algus",
            value=analysis_date,
            min_value=analysis_date,
            max_value=event_date - timedelta(days=7),
            key="plan_start",
        )

        weeks_between = (event_date - plan_start_date).days // 7
        st.caption(f"Plaani pikkus: **{weeks_between} nädalat**, {weeks_between * 7} seansi-kirjet.")

        if st.button("Genereeri treeningkava", type="primary", width="stretch", key="plan_generate"):
            goal = PlanGoal(
                event_name=event_name,
                distance_km=float(distance_km),
                target_time_minutes=float(target_min),
                event_date=event_date,
            )
            summary_for_plan = summarize_load(activities, profile, as_of=analysis_date) if activities else None

            with st.spinner(f"Genereerin {weeks_between}-nädalast kava ({cfg.llm_model})..."):
                try:
                    plan = generate_training_plan(
                        profile=profile,
                        goal=goal,
                        summary=summary_for_plan,
                        plan_start=plan_start_date,
                        config=cfg,
                    )
                except (PlanGenerationError, LLMNotAvailable) as exc:
                    st.error(f"Plaani genereerimine ebaõnnestus: {exc}")
                    st.stop()
                except Exception as exc:
                    st.error(f"Ootamatu viga: {exc}")
                    st.stop()

            st.success(
                f"Plaan valmis: {plan.total_weeks} nädalat, {len(plan.all_sessions)} seanssi "
                f"(mudel: `{plan.model}`)"
            )

            if plan.overview:
                st.markdown("### Ülevaade")
                st.write(plan.overview)

            st.markdown("### Nädalad")
            for week in plan.weeks:
                with st.expander(
                    f"Nädal {week.week_number} ({week.week_start.isoformat()}) — "
                    f"{week.phase.upper()}, siht {week.target_volume_km:.0f} km",
                    expanded=(week.week_number == 1),
                ):
                    if week.notes:
                        st.caption(week.notes)
                    # Stringify numeric columns where missing values use em-dash —
                    # mixed float/str columns make pyarrow log a noisy warning.
                    rows = [
                        {
                            "Kuupäev": s.session_date.isoformat(),
                            "Nädalapäev": ["E", "T", "K", "N", "R", "L", "P"][s.session_date.weekday()],
                            "Tüüp": s.session_type,
                            "Tsoon": s.intensity_zone,
                            "Kestus (min)": int(round(s.duration_min)),
                            "km": f"{s.distance_km:.1f}" if s.distance_km else "—",
                            "Sihttempo": _format_pace(s.target_pace_min_per_km) or "—",
                            "Kirjeldus": s.description,
                        }
                        for s in week.sessions
                    ]
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

            st.markdown("### Eksport")
            csv_bytes = _build_plan_csv(plan)
            st.download_button(
                "Lae alla CSV-failina",
                data=csv_bytes,
                file_name=f"treeningkava_{goal.event_name.replace(' ', '_')}_{goal.event_date.isoformat()}.csv",
                mime="text/csv",
            )
