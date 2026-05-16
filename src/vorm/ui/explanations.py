"""Plain-language explanations of the sports-science metrics shown on screen.

The numbers on the dashboard mean nothing without context — these expanders
turn ACWR / TRIMP / Monotony / CTL-ATL-TSB into one paragraph each, with the
threshold values the rules engine actually uses and a reference for further
reading. Lives in its own module so the markdown blocks don't bloat ``app.py``.
"""

from __future__ import annotations

import streamlit as st

_LOAD_METRICS_BODY = """
##### 📊 ACWR — Acute : Chronic Workload Ratio
**Mis see on:** viimase **7 päeva** keskmise koormuse jagatis viimase **28 päeva**
keskmisega. Näitab, kas viimase nädala töö on **proportsionaalne** sellega,
millega keha on harjunud.

**Sweet-spot 0.8–1.3.** Alla 0.8 → alakoormus (vorm langeb). Üle 1.5 → ületreening,
vigastusrisk u **3-4×** kõrgem (Gabbett 2016, *British Journal of Sports Medicine*).

**Vorm.ai ohutusreegel:** ACWR ≥ 1.5 sunnib soovituse `Vähenda intensiivsust` /
`Taastumispäev` kategooriasse, ükskõik mida LLM soovitab.

---

##### 🔥 TRIMP — Training Impulse (7 p & 28 p)
**Mis see on:** Banisteri TRIMP = `duration_min × HR-intensiivsus × sex_weight`,
kus HR-intensiivsus tuleneb pulsireservist (`(avg_hr − rest) / (max − rest)`),
naistel on eksponentsiaalne kaal 1.67, meestel 1.92.

**7 p TRIMP** = akuutne koormus (väsimus). **28 p TRIMP** = krooniline koormus
(vorm). Kahe suhe annabki ACWR-i.

**Tempo-fallback** kui HR puudub: rTSS-stiilis arvutus, kus intensiivsus =
`(treening-tempo / threshold-tempo)`². Threshold-tempo võetakse profiilist või
tuletatakse 10 km / 5 km PB-st.

---

##### 📈 Monotoonsus — Foster Monotony
**Mis see on:** 7 päeva päevakoormuse **keskmine / standardhälve**. Madal =
varieeruv nädal (kerge + raske + paus). Kõrge = ühepalju iga päev.

- **< 1.5:** terve varieeruvus, taastumine toimub
- **1.5–2.0:** piiripealne, suurenenud risk
- **≥ 2.0:** ohumärk — keha ei saa kuhugi puhkamist (Foster 1998)

**Vorm.ai ohutusreegel:** Monotoonsus ≥ 2.0 + suur 7 p TRIMP → `Lisa taastumispäev`.
"""


_FITNESS_FORM_BODY = """
##### Banister Fitness-Fatigue mudel — CTL / ATL / TSB
Sama TRIMP-i andmed, aga eksponentsiaalne libisev keskmine kahe ajakonstandiga.
Kuni 1990ndateni profitiimide vorm-mudelite alus, tänagi TrainingPeaks'i ja
Strava Fitness-graafiku tuumikalgoritm.

- **CTL (Chronic Training Load)** — 42-päeva eksp. keskmine. Sinine joon. **"Vorm"**
  — kui suurt koormust keha pikaajaliselt talub.
- **ATL (Acute Training Load)** — 7-päeva eksp. keskmine. Punane joon. **"Väsimus"**
  — viimase nädala kumuleerunud koormus.
- **TSB (Training Stress Balance) = CTL − ATL.** Roheline joon paremal teljel.
  **"Form" (võistlusvorm).** Negatiivne → väsinud. **+5 kuni +25 → ideaalne
  võistlusaken** (taper õnnestus). Üle +25 → liiga palju mahalaadimist, vorm
  langeb.

Reegel suurte võistluste eel: **TSB ≥ +5 võistluspäeval**.
"""


def render_load_metrics_explainer() -> None:
    """Expander explaining ACWR / TRIMP / Monotony — pair with the Tab 1 metric row."""
    with st.expander("📖 Mida need näitajad tähendavad?", expanded=False):
        st.markdown(_LOAD_METRICS_BODY)


def render_fitness_form_explainer() -> None:
    """Expander explaining the Banister chart — pair with the Tab 2 chart."""
    with st.expander("📖 Mida tähendab Fitness / Fatigue / Form?", expanded=False):
        st.markdown(_FITNESS_FORM_BODY)
