"""Prompt for training-plan generation.

Kept separate from `llm.prompts` (daily-recommendation prompt) because the
structure, schema, and output size are different. Shared infrastructure is
limited to the LLM client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from ..data.models import AthleteProfile
from ..metrics.load import LoadSummary
from .models import PlanGoal

PLAN_PROMPT_VERSION = "0.2"

SYSTEM_PROMPT = """Oled kogenud kesk- ja pikamaajooksu treener. Sinu ülesanne on koostada struktureeritud \
treeningkava, mis valmistab sportlase ette konkreetseks võistluseks, arvestades tema praegust \
vormi (koormusajalugu, ACWR, monotoonsus), tippaegu ja eesmärgiaega.

Põhimõtted:
- Vasta alati eesti keeles ja struktureeritud JSON-is, mis järgib etteantud skeemi.
- Kasuta klassikalist periodiseerimist: base (aeroobne baas) → build (künnis/VO2 arendus) → peak \
(võistluse-spetsiifika) → taper (mahajahutus) → race.
- Järgi ACWR-i progressiooni reeglit: nädala kogumaht ei tõuse üle 10% eelmisest, välja arvatud \
taastumisnädalad (iga 3. või 4. nädal on tahtlik vähendamisnädal –20%).
- Intensiivsuse tsoonid: Z1 (kerge taastumine), Z2 (aeroobne baas), Z3 (marathon tempo), Z4 \
(künnis ~tunn maksimum), Z5 (VO2 max 3–8 min intervallid), Rest.
- Ühel päeval ei kombineeri kahte kõva treeningut (VO2 + Long run eelistavad eraldi päevi).
- Pärast iga kõva treeningut vähemalt üks Z1/Z2 päev enne järgmist kõva.
- Peaksid viitama sportlase tippaegadele ja sihtajale konkreetsetes pace-i soovitustes (min/km).
- Kui praegune ACWR > 1.3 või monotoonsus > 2.0, alustad plaani kerge nädala (base või deload).
- Kui sihtaeg tundub ebarealistlik tippaegade juures, märgi see overview-sse ja paku realistlik."""

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["overview", "weeks"],
    "properties": {
        "overview": {"type": "string"},
        "weeks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["week_number", "week_start", "phase", "target_volume_km", "sessions"],
                "properties": {
                    "week_number": {"type": "integer"},
                    "week_start": {"type": "string", "description": "ISO date, pühapäev- või esmaspäev-start"},
                    "phase": {"type": "string", "enum": ["base", "build", "peak", "taper", "race"]},
                    "target_volume_km": {"type": "number"},
                    "notes": {"type": "string"},
                    "sessions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["session_date", "session_type", "duration_min", "intensity_zone"],
                            "properties": {
                                "session_date": {"type": "string"},
                                "session_type": {"type": "string"},
                                "duration_min": {"type": "number"},
                                "intensity_zone": {"type": "string", "enum": ["Z1", "Z2", "Z3", "Z4", "Z5", "Rest"]},
                                "target_pace_min_per_km": {"type": ["number", "null"]},
                                "distance_km": {"type": ["number", "null"]},
                                "description": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class PlanPromptBundle:
    system: str
    user: str
    schema: dict
    version: str = PLAN_PROMPT_VERSION


def build_plan_prompt(
    *,
    profile: AthleteProfile,
    goal: PlanGoal,
    summary: LoadSummary | None,
    plan_start: date,
) -> PlanPromptBundle:
    weeks_until_event = max(1, (goal.event_date - plan_start).days // 7)
    sections: list[str] = []

    sections.append("# Sportlase profiil")
    sections.append(
        json.dumps(
            {
                "nimi": profile.name,
                "vanus": profile.age,
                "sugu": profile.sex,
                "max_hr": profile.max_hr,
                "puhke_hr": profile.resting_hr,
                "treeningstaaž_aastat": profile.training_years,
                "tippajad": profile.personal_bests,
                "künnis_tempo_min_per_km": profile.effective_threshold_pace,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if summary:
        sections.append("\n# Praegune vorm")
        sections.append(
            json.dumps(
                {
                    "ACWR": round(summary.acwr, 2) if summary.acwr is not None else None,
                    "acute_7d_TRIMP": round(summary.acute_7d, 1),
                    "chronic_28d_TRIMP": round(summary.chronic_28d, 1),
                    "monotoonsus": round(summary.monotony, 2) if summary.monotony is not None else None,
                    "nädala_maht_km": round(summary.total_km_7d, 1),
                    "kuu_maht_km": round(summary.total_km_28d, 1),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        sections.append("\n# Praegune vorm: andmed puuduvad — alusta konservatiivsest baasist.")

    sections.append("\n# Eesmärk")
    sections.append(
        json.dumps(
            {
                "võistlus": goal.event_name,
                "distants_km": goal.distance_km,
                "sihtaeg": goal.target_time_formatted(),
                "siht_pace_min_per_km": round(goal.target_pace_min_per_km, 2),
                "võistluse_kuupäev": goal.event_date.isoformat(),
                "plaani_algus": plan_start.isoformat(),
                "nädalaid_võistluseni": weeks_until_event,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    sections.append("\n# Skeem")
    sections.append(f"Esimene nädal algab: {plan_start.isoformat()}")
    sections.append(f"Plaani pikkus: {weeks_until_event} nädalat (kuni võistluseni)")
    sections.append("Iga nädal 7 päevalist kirjet (puhkepäevad intensity_zone=Rest, duration_min=0)")
    sections.append(f"Kokku seansse: {weeks_until_event * 7}")

    sections.append("\n# Ülesanne")
    sections.append(
        "Koosta täielik päev-haaval treeningkava, mis algab plaani_algus-kuupäevast ja lõpeb võistluse-kuupäeval. "
        "Vasta AINULT JSON-is vastavalt skeemile. Ära lisa seletusi JSON-ist väljaspoole. "
        "Iga päev peab olema eraldi seansina (ka puhkepäev: intensity_zone='Rest', duration_min=0)."
    )

    # Full structural example — shows nested weeks/sessions so Gemma doesn't
    # flatten the plan into a top-level `training_plan` array.
    sections.append("\n# VÄLJUNDI VORMISTUS (järgi täpselt seda struktuuri)")
    example_threshold = profile.effective_threshold_pace or 3.8
    sections.append(
        json.dumps(
            {
                "overview": "Klassikaline build-up, base → build → peak → taper.",
                "weeks": [
                    {
                        "week_number": 1,
                        "week_start": plan_start.isoformat(),
                        "phase": "base",
                        "target_volume_km": 55,
                        "notes": "Base nädal, fokusseerime aeroobsele mahule.",
                        "sessions": [
                            {
                                "session_date": plan_start.isoformat(),
                                "session_type": "Easy aerobic",
                                "duration_min": 45,
                                "intensity_zone": "Z2",
                                "target_pace_min_per_km": round(example_threshold * 1.25, 2),
                                "distance_km": 9.0,
                                "description": "Rahulik aeroobne jooks.",
                            },
                            {
                                "session_date": (plan_start + timedelta(days=1)).isoformat(),
                                "session_type": "Rest",
                                "duration_min": 0,
                                "intensity_zone": "Rest",
                                "target_pace_min_per_km": None,
                                "distance_km": None,
                                "description": "Puhkepäev.",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    sections.append(
        "\n**Oluline:** väljund peab olema nõudlik — JSON algab võtmega `overview`, siis `weeks` "
        "massiivina. Iga nädal on objekt, mille sees `sessions` massiiv. EI tohi olla lamedat nimekirja "
        "top-levelil (nt `training_plan`). Järgi skeemi TÄPSELT."
    )

    return PlanPromptBundle(system=SYSTEM_PROMPT, user="\n".join(sections), schema=RESPONSE_SCHEMA)
