# jooksuai

**AI-põhine treeningkoormuse analüüsija kesk- ja pikamaajooksjatele.**

Tööriist võtab sisse sinu viimased treeningud (Strava / Garmini CSV / näidisandmed), arvutab spordimeditsiinilised koormusnäitajad (ACWR, TRIMP, monotoonsus), käivitab ohutusreeglite filtrid ja küsib suurest keelemudelist (Claude või GPT) konkreetse soovituse tänase treeningu kohta koos loomuliku keele põhjendusega.

Projekt valmib Tallinna Tehnikaülikooli *Tehisintellekti rakendamine*-aine raames kevadel 2026. Autor: Uku Renek Kronbergs.

---

## Miks seda on vaja

Harrastus- ja poolprofessionaalsetel jooksjatel on palju andmeid (nutikell, GPS, pulss, uni), aga vähe aega neid struktureeritult analüüsida. Olemasolevad tööriistad annavad kas ainult numbreid (Garmin Training Readiness) või maksavad palju ja eeldavad treeneri-tasemel tõlgendusoskust (TrainingPeaks). `jooksuai` annab **andmepõhise teise arvamuse** tänase planeeritud treeningu kohta — kas seda peaks jätkama, vähendama, asendama või vahele jätma — koos inimkeele põhjendusega, mis viitab konkreetsetele numbritele.

## Mis rakendus teeb

- **Ostab endasse** viimase 60 päeva treeningud (Strava API / Strava-eksport CSV / lokaalne näidisandmestik).
- **Arvutab** akuutne 7-päeva koormus, krooniline 28-päeva koormus, ACWR, Banisteri TRIMP, Fosteri monotoonsus ja strain.
- **Käivitab ohutusreeglid** — kui ACWR > 1.5, RPE ≥ 8 kaks päeva järjest, haigus või uni < 6 h, sunnitakse vastus ohutu kategooria suunas.
- **Küsib LLM-ilt soovituse** neljas kategoorias (jätka / vähenda / taastumispäev / alternatiivne) koos 2–4-lauselise põhjendusega.
- **Näitab** ACWR-kõverat, päevakoormust, nädalamahtu ja RPE-trendi Plotly-interaktiivgraafikutena.
- **Retrospektiivne test** — vali mineviku kuupäev, näita mudeli soovitust nii, nagu see päev oleks olnud täna.

## Kiire alustamine

```bash
# 1. Kloonige
git clone https://github.com/ukurenek/jooksuai.git
cd jooksuai

# 2. Virtual environment + sõltuvused
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                    # registreerib `jooksuai` paketi Pythoni teele

# 3. (Valikuline) LLM ja Strava võtmed
cp .env.example .env
# Sisesta oma ANTHROPIC_API_KEY (või OPENAI_API_KEY) .env-faili

# 4. Käivita
streamlit run app.py
```

Esimesel käivitamisel kasuta **Näidisandmed**-valikut — 90 päeva deterministlikult genereeritud näidisjooksu valmistavad terve UI demoks ette. Ilma LLM-võtmeta jookseb kõik peale soovituse teksti — ACWR, graafikud ja reeglitepõhine vastus töötavad ka offline.

## Andmete formaat

Rakendus toetab kaht CSV-formaati:

**Natiivne** (soovituslik, kasutab rakendus ise lokaalses vahemälus):
```csv
id,activity_date,activity_type,distance_km,duration_min,avg_hr,max_hr_observed,avg_pace_min_per_km,elevation_gain_m,rpe,notes
a1,2026-04-18,Run,10.5,48.5,148,172,4.62,55,5,Easy aerobic
```

**Strava eksport** (https://www.strava.com/athlete/delete_your_account → Get a copy of your data):
```csv
Activity ID,Activity Date,Activity Type,Distance,Elapsed Time,Average Heart Rate,Max Heart Rate,Elevation Gain,Activity Name
12345,"Apr 18, 2026, 06:00:00 AM",Run,10500,2910,148,172,55,Morning Run
```

Rakendus tuvastab formaadi veergude järgi automaatselt.

## Arhitektuur

```
src/jooksuai/
├── config.py           # env-põhine konfiguratsioon (Config dataclass)
├── data/
│   ├── models.py       # TrainingActivity, AthleteProfile, DailySubjective
│   ├── storage.py      # SQLite vahemälu
│   ├── strava.py       # stravalib-põhine OAuth-klient
│   ├── csv_loader.py   # Natiivne + Strava-eksport parser
│   └── sample.py       # Deterministlik näidisgeneraator
├── metrics/
│   └── load.py         # TRIMP, ACWR, monotoonsus, strain
├── rules/
│   └── safety.py       # Reeglipõhised ohutusfiltrid (Plan B3)
├── llm/
│   ├── prompts.py      # Eestikeelne prompt + few-shot näited
│   └── client.py       # Anthropic + OpenAI taustakliendid
└── ui/
    └── charts.py       # Plotly graafikud
app.py                  # Streamlit entry point
```

Andmevoog:
```
Strava/CSV/Sample ─► TrainingActivity[] ─► Metrics (ACWR, TRIMP) ─► Safety rules ─► LLM prompt ─► JSON soovitus ─► UI
```

## Arenduskäik

```bash
# Testid
pytest
pytest --cov=src/jooksuai

# Linting (valikuline)
pip install ruff
ruff check .
```

Unit-testid katavad praegu:
- Banisteri TRIMP-i käsitsi arvutatud referentsväärtused
- ACWR konvergeerub 1.0-le konstantse koormuse juures
- ACWR hüppab ohupiirile, kui 7-päeva koormus kolmekordistub
- Monotoonsus = None nullvariantsi puhul
- Safety rules — iga reegli fire-kontekst + precedence order
- CSV-parseri mõlemad formaadid

CI töötab GitHub Actionsis iga push-i peal Python 3.11 ja 3.12 all.

## Valideerimisplaan

Projekt valideeritakse kolmes etapis (vt [PROJECT_PLAN.md](PROJECT_PLAN.md) jaotis 4):

1. **Retrospektiivne test** 30 varasemal päeval (sh 5–7 teadaolevalt „kriitilist" päeva). Edu = ≥ 70% kattumist mu omaaegse otsusega.
2. **Treeneri kõrvutus** 14 järjestikusel päeval (18.05 – 01.06). Treener Ardi Vann hindab samu sisendeid ilma mudeli väljundit nägemata.
3. **Kvalitatiivne intervjuu** 2 treeningkaaslasega projekti lõpus.

## Privaatsus

- Treeningandmed (GPS-punktid, tooraine pulsiribaread) **ei** liigu LLM-pakkuja serverisse — LLM näeb ainult agregeeritud näitajaid ja metaandmeid.
- Strava refresh token hoitakse lokaalses `.env`-failis (gitignored) ja SQLite vahemälus.
- Multi-user tugi ja avalik deploy pole MVP-s — vt projekti plaani „5. Riskide maandamine".

## Vastutuspiir

Tööriist on **otsustustugi**, mitte asendaja treenerile ega arstile. Vigastuse, haiguse või treeningplaani põhimõttelise küsimuse puhul pöördu oma treeneri või arsti poole. Soovitus on sama usaldusväärne kui sisendandmed ja mudeli tõlgendus — kriitilist mõtlemist ei saa sellele delegeerida.

## Litsents

MIT — vt [LICENSE](LICENSE).

## Autor

Uku Renek Kronbergs ([@ukurenek](https://github.com/ukurenek))
