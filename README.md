# Vorm.ai

**AI-põhine treeningkoormuse analüüsija kesk- ja pikamaajooksjatele.**

Tööriist võtab sisse sinu viimased treeningud (Strava / Garmini CSV / Polari JSON-eksport / näidisandmed), arvutab spordimeditsiinilised koormusnäitajad (TRIMP, ACWR, Banister CTL/ATL/TSB, monotoonsus), käivitab ohutusreeglite filtrid, tuvastab tippajad ja küsib suurest keelemudelist konkreetse soovituse tänase treeningu kohta koos loomuliku keele põhjendusega.

Projekt valmib Tartu Ülikooli *Tehisintellekti rakendamine*-aine raames kevadel 2026. Autor: Uku Renek Kronbergs.

---

## Miks seda on vaja

Harrastus- ja poolprofessionaalsetel jooksjatel on palju andmeid (nutikell, GPS, pulss, uni), aga vähe aega neid struktureeritult analüüsida. Olemasolevad tööriistad annavad kas ainult numbreid (Garmin Training Readiness) või maksavad palju ja eeldavad treeneri-tasemel tõlgendusoskust (TrainingPeaks). **Vorm.ai** annab **andmepõhise teise arvamuse** tänase planeeritud treeningu kohta — kas seda peaks jätkama, vähendama, asendama või vahele jätma — koos inimkeele põhjendusega, mis viitab konkreetsetele numbritele.

## Mis rakendus teeb

- **Ostab endasse** viimase 60 päeva treeningud (Strava API cache'iga / Strava-eksport CSV / Garmin GPX-kaust / lokaalne näidisandmestik).
- **Arvutab** akuutne 7-päeva koormus, krooniline 28-päeva koormus, ACWR, Banisteri TRIMP, Fosteri monotoonsus ja strain. Pulsiandmete puudumisel kasutab tempo-põhist rTSS-stiilis fallback'i (künnis-tempo tuletub 10 km PB-st).
- **Käivitab ohutusreeglid** — kui ACWR > 1.5, RPE ≥ 8 kaks päeva järjest, haigus või uni < 6 h, sunnitakse vastus ohutu kategooria suunas.
- **Prognoosib ACWR-trendi** scikit-learn lineaarse regressiooniga (viimased 14 päeva) — hoiatab juba enne, kui kasvav trend lõikab läbi ohulõike 1.5.
- **Küsib LLM-ilt soovituse** neljas kategoorias (jätka / vähenda / taastumispäev / alternatiivne) koos 2–4-lauselise põhjendusega. Toetab 3 prompti-varianti A/B-testimiseks (`baseline` / `numeric` / `conservative`).
- **Näitab** ACWR-kõverat, päevakoormust, nädalamahtu ja RPE-trendi Plotly-interaktiivgraafikutena.
- **Retrospektiivne test** — vali mineviku kuupäev, näita mudeli soovitust nii, nagu see päev oleks olnud täna.
- **Päeva-päeva kasutusslog** — pärast soovitust salvesta 1–5 hinnang kasulikkusele ja veenvusele, kas järgisid, ja järgmise treeningu enesetunne. Vajalik valideerimise §4.3 jaoks.
- **Treeningkava** — genereerib täieliku päev-haaval struktureeritud võistluse-ettevalmistuse kava (base → build → peak → taper), arvestades sinu praegust vormi ja tippaegu. CSV-eksport TrainingPeaksi-sõbralik.

## Kiire alustamine

```bash
# 1. Kloonige
git clone https://github.com/UkuRenekKronbergs/vorm.git
cd vorm

# 2. Virtual environment + sõltuvused
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                    # registreerib `vorm` paketi Pythoni teele

# 3. (Valikuline) LLM ja Strava võtmed
cp .env.example .env
# Sisesta üks järgmistest: ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY.
# Provideri valimiseks sea LLM_PROVIDER=anthropic|openai|openrouter (vaikimisi: anthropic).
# Mudeli valimiseks sea LLM_MODEL (nt openrouter puhul: anthropic/claude-sonnet-4.6,
# deepseek/deepseek-v4-flash, meta-llama/llama-3.3-70b-instruct jne).

# 4. (Valikuline) Strava OAuth — üks kord, produceerib refresh_tokeni
python scripts/strava_bootstrap.py

# 5. Käivita
streamlit run app.py
```

Esimesel käivitamisel kasuta **Näidisandmed**-valikut — 90 päeva deterministlikult genereeritud näidisjooksu valmistavad terve UI demoks ette. Ilma LLM-võtmeta jookseb kõik peale soovituse teksti — ACWR, graafikud ja reeglitepõhine vastus töötavad ka offline.

### Strava-andmete vahemälu

Kui valid sidebari **Strava API**, kasutab rakendus `fetch_with_cache()`-i: lokaalsesse SQLite-faili (`data/cache/activities.sqlite`) salvestatakse iga kunagi päritud treening. Igal järgneval käivitusel küsitakse Stravalt ainult delta (alates viimase cache-treeningu kuupäevast − 1 päev, et viimase päeva nimetuse muudatused korjata). API tõrke korral (429, võrk maas) tagastatakse vahemälu sisu — sünk ei kaota andmeid. Vt projekti plaan §5 Risk 2.

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
src/vorm/
├── config.py                # env-põhine konfiguratsioon (Config dataclass)
├── data/
│   ├── models.py            # TrainingActivity, AthleteProfile, DailySubjective
│   ├── storage.py           # SQLite vahemälu + päeva-logi (§4.3)
│   ├── strava.py            # stravalib OAuth-klient + cache-teadlik delta-sync
│   ├── garmin.py            # GPX-kaust fallback parser (§5 Risk 2)
│   ├── csv_loader.py        # Natiivne + Strava-eksport parser
│   ├── polar.py             # Polar Flow JSON → Strava HR-täiendus
│   └── sample.py            # Deterministlik näidisgeneraator
├── metrics/
│   ├── load.py              # TRIMP, ACWR, monotoonsus, Banister CTL/ATL/TSB, RPE-süntees
│   ├── forecast.py          # sklearn ACWR-trend prognoos (§2 statistiline turvafilter)
│   └── personal_bests.py    # Tippajad standard-distantsidele
├── rules/
│   └── safety.py            # Reeglipõhised ohutusfiltrid
├── llm/
│   ├── prompts.py           # Igapäevane prompt + few-shot näited + 3 A/B-varianti
│   ├── _json_utils.py       # Tolerantne JSON-parser (avatud mudelite jaoks)
│   └── client.py            # Anthropic + OpenAI + OpenRouter taustakliendid
├── planning/
│   ├── models.py            # PlanGoal, PlannedSession, WeekPlan, TrainingPlan
│   ├── prompts.py           # Treeningkava prompt + JSON skeem
│   └── generator.py         # LLM orkestreerimine, tolerantne parser
└── ui/
    └── charts.py            # Plotly graafikud
app.py                       # Streamlit entry point
scripts/
├── enrich_strava_with_polar.py   # CLI: Polari pulsiandmed Strava CSV-sse
├── strava_bootstrap.py           # CLI: Strava OAuth refresh-token genereerimine
└── validate.py                   # CLI: PROJECT_PLAN §4 valideerimisharness
```

Andmevoog:
```
Strava/CSV/Sample ─► TrainingActivity[] ─► Metrics (ACWR, TRIMP) ─► Safety rules ─► LLM prompt ─► JSON soovitus ─► UI
```

## Arenduskäik

```bash
# Testid
pytest
pytest --cov=src/vorm

# Linting (valikuline)
pip install ruff
ruff check .
```

Unit-testid katavad praegu (106 testi):
- Banisteri TRIMP-i käsitsi arvutatud referentsväärtused
- ACWR konvergeerub 1.0-le konstantse koormuse juures
- ACWR hüppab ohupiirile, kui 7-päeva koormus kolmekordistub
- Monotoonsus = None nullvariantsi puhul
- Safety rules — iga reegli fire-kontekst + precedence order
- CSV-parseri mõlemad formaadid
- Strava delta-sync: külm/soe vahemälu, API tõrke fallback, mitte-jooks filtreering
- Garmin GPX-parser: HR-aggregatsioon, mitte-jooksu filtreering, kaust-laadimine
- ACWR-trend regressioon: tasakaalu trend, danger-crossing, müra-supressioon
- Päeva-logi SQLite roundtrip + upsert + skaala-valideerimine

CI töötab GitHub Actionsis iga push-i peal Python 3.11 ja 3.12 all.

## Valideerimisplaan

Projekt valideeritakse nelja etapina (vt [PROJECT_PLAN.md](PROJECT_PLAN.md) jaotis 4):

1. **Retrospektiivne test** 30 varasemal päeval (sh 5–7 teadaolevalt „kriitilist" päeva). Edu = ≥ 70% kattumist mu omaaegse otsusega.
2. **Treeneri kõrvutus** 14 järjestikusel päeval (18.05 – 01.06). Treener Ille Kukk hindab samu sisendeid ilma mudeli väljundit nägemata.
3. **Isiklik igapäevane kasutus** 14 päeva järjest — UI-s päeva-logi (`Päeva-logi` tab), kus iga päev login: kasulikkus (1–5), veenvus (1–5), kas järgisin, järgmise treeningu enesetunne (1–5).
4. **Kvalitatiivne intervjuu** 2 treeningkaaslasega projekti lõpus. Skript: [docs/interview_script.md](docs/interview_script.md) (6 pool-struktureeritud küsimust).

### Valideerimisharness

[`scripts/validate.py`](scripts/validate.py) on automatiseeritud harness etappide 1 ja 2 jaoks. Kasutab pikendatud näidisandmestikku (Jan 2026 – 1. juuni 2026, sh kaks tehislikult induce'itud ülekoormusakent ja üks haiguseaken), arvutab iga päeva koormusnäitajad sama torujuhtmega nagu live-rakendus, ja võrdleb mudeli soovitust simuleeritud sportlase + treeneri otsustega. Päris valideerimine asendab simuleeritud otsused logitud tõe-väärtustega.

```bash
# Reegli-režiim (offline, kohene, deterministlik)
python scripts/validate.py

# LLM-režiim — kasutab .env-i providerit (Anthropic / OpenAI / OpenRouter)
python scripts/validate.py --llm

# Sundi värsked LLM-päringud (ignoreeri validation_llm_cache.json)
python scripts/validate.py --llm --no-cache

# Suitsutest — 5 päeva kummalgi etapil
python scripts/validate.py --llm --limit 5

# A/B-testi prompti varianti (baseline / numeric / conservative)
python scripts/validate.py --llm --prompt-variant numeric
```

Väljundid:
- `validation_report.md` — markdown-aruanne (kokkuvõte, metoodika, päeva-tabelid, lahkuminekute klassifikatsioon).
- `validation_data.csv` — per-day võrdlustabel.
- `validation_llm_cache.json` — ainult `--llm` režiimis; LLM-vastused (cache-võti = sisendi hash + mudel + prompti versioon, automaatne invalideerimine).

## Tasuta deploy — Streamlit Community Cloud

Rakendus on cloud-deploy-valmis. Failisüsteemil ei pea olema kirjeldatud sõltuvusi peale `requirements.txt`-i; saladused tulevad Streamlit Cloud'i settings'ist.

### Sammud

1. **Logi sisse** [share.streamlit.io](https://share.streamlit.io) GitHubi kaudu.
2. **Deploy app** → repo: `UkuRenekKronbergs/vorm`, branch: `main`, main file: `app.py`.
3. **Advanced settings → Python version:** vali `3.13` (matches [`runtime.txt`](runtime.txt)).
4. **App settings → Secrets** — kleebi TOML-vormingus (näide OpenRouteri tasuta GPT-OSS 120B-ga):
   ```toml
   LLM_PROVIDER = "openrouter"
   LLM_MODEL = "openai/gpt-oss-120b:free"
   LLM_TEMPERATURE = "0"
   OPENROUTER_API_KEY = "sk-or-v1-..."
   # Tasuta alternatiivid (kontrollitud 2026-05 seisuga, kvaliteedi järgi):
   # LLM_MODEL = "nousresearch/hermes-3-llama-3.1-405b:free"   # suurim avatud mudel
   # LLM_MODEL = "meta-llama/llama-3.3-70b-instruct:free"     # kiire, mõnikord rate-limited
   # LLM_MODEL = "deepseek/deepseek-v4-flash:free"             # 1M context, tugev arutlus
   # Tasuline alternatiiv (kõrgeim kvaliteet, ~$0.01-0.05 päringu kohta):
   # LLM_MODEL = "anthropic/claude-sonnet-4.6"
   # Otse-provideri võtmed (kui ei kasuta OpenRouter'it):
   # ANTHROPIC_API_KEY = "sk-ant-..."
   # OPENAI_API_KEY    = "sk-..."
   # Strava (valikuline — ilma selleta peita "Strava API" valik UI-st):
   # STRAVA_CLIENT_ID     = "12345"
   # STRAVA_CLIENT_SECRET = "..."
   # STRAVA_REFRESH_TOKEN = "..."  # genereeri lokaalselt: python scripts/strava_bootstrap.py
   ```
5. Saad URL-i kujul `https://vorm-ai.streamlit.app`.

Cloud-režiimis vaikimisi andmeallikas on **Näidisandmed** — täielik demo töötab ilma isikuandmeteta.

### Cloud-spetsiifika

- **Failisüsteem on efemeerne.** SQLite vahemälu (`data/cache/activities.sqlite`) ja päeva-logi kaovad iga restardi peal. Ühe-kasutaja demo jaoks pole probleem; mitme-kasutaja kasutuseks asenda `ActivityStore` Supabase'i / Turso / Neon'i peale (~2-3 h töö).
- **App magab 7 päeva idle järel.** Esmase päringu cold-start ~30 s.
- **Strava OAuth** — `scripts/strava_bootstrap.py` kasutab localhost:8000-i ega tööta cloud'is. Genereeri token lokaalselt, kleebi `STRAVA_REFRESH_TOKEN` Streamlit secrets'i.
- **RAM ~1 GB.** Praegune sõltuvuste komplekt (Streamlit + pandas + scikit-learn) mahub ära ~400 MB peal.

### Konfiguratsiooni resolutsioon

`vorm.config.load_config()` otsib iga võtit kahest kohast (esimese leitu võidab):

1. Process environment / `.env`-fail (lokaalne arendus, `python-dotenv` laeb)
2. `streamlit.secrets` (Streamlit Cloud)

Sama kood töötab mõlemas keskkonnas.

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
