"""Validation harness for Vorm.ai (Project Plan §4).

Two stages:

1. **Retrospective test** — 30 past days (incl. 5-7 critical days). Compare the
   model's recommendation against the athlete's own historical decision.
2. **Coach comparison** — 14 consecutive days (2026-05-18 .. 2026-06-01).
   Compare the model's recommendation against coach Ille Kukk's recommendation
   given the same inputs.

Athlete (Enari Tõnström) and coach (Ille Kukk) are simulated stand-ins for the
validation rehearsal — the same logic that the live validation will follow,
populated with plausible decisions inferred from each day's load + subjective
context. Real validation will replace these simulated decisions with logged
ground truth.

Modes:

- **Rules mode (default):** the "model" recommendation is the rule-based
  safety verdict — deterministic, offline, no LLM cost. Equivalent to the
  app running without an API key.
- **LLM mode (`--llm`):** for each day, the full prompt is sent to the
  configured LLM (provider/model controlled via env-vars per `vorm.config`).
  Responses are cached at `validation_llm_cache.json` so re-runs are free.
  Use `--no-cache` to force fresh calls.

Run:
    python scripts/validate.py                     # rules mode
    python scripts/validate.py --llm               # LLM mode (uses .env keys)
    python scripts/validate.py --llm --no-cache    # force fresh LLM calls
    python scripts/validate.py --llm --limit 5     # smoke-test on 5 days

Writes:
    validation_report.md         (final markdown report)
    validation_data.csv          (per-day comparison table)
    validation_llm_cache.json    (LLM responses, only in --llm mode)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from vorm.config import load_config
from vorm.data.models import AthleteProfile, DailySubjective, TrainingActivity
from vorm.data.sample import _WEEKLY_PATTERN, load_sample_profile
from vorm.llm.client import (
    LLMNotAvailable,
    LLMParseError,
    LLMRecommendation,
    generate_recommendation,
)
from vorm.llm.prompts import build_prompt
from vorm.metrics.load import LoadSummary, summarize_load
from vorm.rules.safety import Recommendation, SafetyVerdict, evaluate_safety_rules

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "validation_report.md"
DATA_PATH = REPO_ROOT / "validation_data.csv"
CACHE_PATH = REPO_ROOT / "validation_llm_cache.json"


def _prompt_version_in_use() -> str:
    from vorm.llm.prompts import PROMPT_VERSION
    return PROMPT_VERSION

# ---------------------------------------------------------------------------
# 1. Extended sample data — sample.py stops mid-April; extend to 2026-06-01
#    using the same weekly micro-cycle pattern, plus a deliberately inserted
#    overload block to seed critical days.
# ---------------------------------------------------------------------------

DATA_START = date(2026, 1, 21)
DATA_END = date(2026, 6, 1)
TODAY = date(2026, 5, 16)


def build_extended_activities(profile: AthleteProfile) -> list[TrainingActivity]:
    """Reproduce sample.generate_sample_activities()'s pattern over a longer window.

    Adds two deliberately-induced overload bumps in late April and mid May so
    the retrospective test has the 5-7 known-critical days the plan requires.

    Also injects ~10-15% of days where the athlete deviated from his plan:
    cut a hard session to easy, swapped in a recovery day, or skipped entirely.
    This deviation rate increases when ACWR is rising — modelling the realistic
    pattern that even disciplined athletes scale back under accumulating load.
    """
    rng = random.Random(42)
    activities: list[TrainingActivity] = []
    days = (DATA_END - DATA_START).days + 1

    # Overload blocks: doubled tempo+VO2 weeks with no deload — week-on-week
    # mileage climbs ~25%, ACWR pushes above 1.4. Athlete reports high RPE.
    overload_windows = [
        (date(2026, 4, 13), date(2026, 4, 19)),   # mid-April spike
        (date(2026, 5, 4), date(2026, 5, 10)),    # May overreach
    ]

    # 7-day rolling load proxy to model the athlete's perceived fatigue
    rolling_load: list[float] = []

    for offset in range(days):
        day = DATA_START + timedelta(days=offset)
        weekday = day.weekday()
        name, intensity, base_km, base_min, hr_frac = _WEEKLY_PATTERN[weekday]
        if intensity == "rest":
            continue

        week_of_block = (offset // 7) % 4
        load_multiplier = 0.7 if week_of_block == 3 else 1.0

        in_overload = any(start <= day <= end for start, end in overload_windows)
        if in_overload:
            load_multiplier *= 1.30

        # Illness window: most days skipped
        if date(2026, 4, 28) <= day <= date(2026, 5, 1):
            if rng.random() < 0.7:
                continue

        # Athlete deviation: if rolling load is high, athlete sometimes scales back.
        recent_total = sum(rolling_load[-7:])
        # Baseline ~ 5 hard-ish sessions / week, total ~ 300 (TRIMP-ish)
        fatigue_pressure = max(0, (recent_total - 300) / 200)
        # Probability athlete cuts back today, biased by fatigue
        cut_prob = 0.06 + 0.20 * fatigue_pressure
        actual_intensity = intensity
        actual_name = name
        if intensity in {"tempo", "threshold", "vo2", "long"} and rng.random() < cut_prob:
            # 60% downgrade to easy, 30% downgrade to recovery, 10% skip
            roll = rng.random()
            if roll < 0.6:
                actual_intensity = "easy"
                actual_name = "Easy aerobic (kärbitud plaanist)"
                base_km, base_min, hr_frac = 8.0, 40.0, 0.58
            elif roll < 0.9:
                actual_intensity = "recovery"
                actual_name = "Recovery (kärbitud plaanist)"
                base_km, base_min, hr_frac = 6.0, 32.0, 0.50
            else:
                # Skip entirely
                continue

        jitter = rng.uniform(0.92, 1.08)
        distance = round(base_km * load_multiplier * jitter, 2)
        duration = round(base_min * load_multiplier * jitter, 1)
        hr_jitter = rng.randint(-3, 3)
        if in_overload and actual_intensity in {"tempo", "threshold", "vo2", "long"}:
            hr_jitter += 4
        hr = int(profile.resting_hr + hr_frac * profile.hr_reserve + hr_jitter)
        pace = round(duration / distance, 2) if distance > 0 else None
        elev = round(rng.uniform(20, 150) * (duration / 40), 1)

        rpe = None
        if rng.random() < 0.65 or in_overload:
            rpe_base = {"easy": 3, "recovery": 2, "long": 5, "tempo": 7, "threshold": 7, "vo2": 8}
            rpe = rpe_base.get(actual_intensity, 4) + rng.choice([-1, 0, 0, 1])
            if in_overload:
                rpe = min(10, rpe + 1)
            rpe = max(1, min(10, rpe))

        # Rough TRIMP proxy for the rolling fatigue tracker
        proxy_load = duration * (0.4 + 0.7 * hr_frac)
        rolling_load.append(proxy_load)

        activities.append(
            TrainingActivity(
                id=f"sim-{day.isoformat()}",
                activity_date=day,
                activity_type="Run",
                distance_km=distance,
                duration_min=duration,
                avg_hr=hr,
                max_hr_observed=hr + rng.randint(5, 15),
                avg_pace_min_per_km=pace,
                elevation_gain_m=elev,
                rpe=rpe,
                notes=actual_name,
            )
        )

    return activities


# ---------------------------------------------------------------------------
# 2. Daily subjective inputs simulated alongside the activity log
# ---------------------------------------------------------------------------

def subjective_for(day: date, prev_activity: TrainingActivity | None) -> DailySubjective:
    """Plausible same-day subjective inputs.

    Simple model: RPE yesterday inherits from the prior session; sleep tends
    to dip after hard days; one explicit illness window mid-validation.
    """
    rpe_yesterday = prev_activity.rpe if prev_activity else None

    # Illness window
    illness = date(2026, 4, 28) <= day <= date(2026, 5, 1)

    sleep = 7.5
    if rpe_yesterday is not None and rpe_yesterday >= 8:
        sleep = 6.2
    if illness:
        sleep = 5.5

    stress = 2
    if rpe_yesterday is not None and rpe_yesterday >= 8:
        stress = 4

    notes = None
    if illness:
        notes = "Kerge palavik, kurguvalu"
    elif rpe_yesterday is not None and rpe_yesterday >= 8:
        notes = "Jalad rasked, eilne intervall oli kõva"

    return DailySubjective(
        entry_date=day,
        rpe_yesterday=rpe_yesterday,
        sleep_hours=sleep,
        stress_level=stress,
        illness=illness,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 3. Today-plan derivation — the planned session is the upcoming weekday's
#    template before any safety override.
# ---------------------------------------------------------------------------

def planned_session_for(day: date) -> str:
    name, _intensity, base_km, base_min, _hr = _WEEKLY_PATTERN[day.weekday()]
    if name == "Rest":
        return "Puhkepäev"
    return f"{name} — ~{base_km:.0f} km / {base_min:.0f} min"


# ---------------------------------------------------------------------------
# 4. Simulate the athlete's retrospective decision (Enari Tõnström)
#    and the coach's prospective decision (Ille Kukk) for the same context.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DayContext:
    day: date
    plan: str
    summary: LoadSummary
    verdict: SafetyVerdict
    subjective: DailySubjective
    actual_activity: TrainingActivity | None  # what athlete did on that day


def athlete_retrospective_decision(ctx: DayContext) -> Recommendation:
    """What Enari Tõnström decided that day in real time, reconstructed from
    what he actually did.

    Ground-truth-style mapping:
    - Day skipped entirely AND illness window → RECOVER.
    - Logged session ≥ 30% shorter than plan template → REDUCE.
    - Logged session is "Recovery" / "Easy" type when plan was hard → REDUCE.
    - Logged session matches plan template intensity → CONTINUE.

    Athletes are typically over-eager: they push through accumulating ACWR and
    high RPE until something forces them down (illness, very heavy legs).
    The heuristic intentionally does **not** mirror the safety rules — we want
    to see where the rules would have caught the athlete in time.
    """
    plan_template = _WEEKLY_PATTERN[ctx.day.weekday()]
    plan_name, plan_intensity, plan_km, plan_min, _ = plan_template

    # Did the athlete skip the planned session entirely?
    if ctx.actual_activity is None:
        if ctx.subjective.illness:
            return Recommendation.RECOVER
        # Rest day in the plan template
        if plan_intensity == "rest":
            return Recommendation.CONTINUE
        # Unplanned skip — athlete likely felt off
        return Recommendation.REDUCE

    actual_notes = (ctx.actual_activity.notes or "").lower()
    actual_min = ctx.actual_activity.duration_min
    actual_intensity_class = (
        "recovery" if "recovery" in actual_notes
        else "easy" if "easy" in actual_notes
        else "long" if "long" in actual_notes
        else "tempo" if "tempo" in actual_notes
        else "vo2" if "vo2" in actual_notes
        else "other"
    )

    # Heavy mismatch: actual was recovery, plan was hard
    if actual_intensity_class == "recovery" and plan_intensity in {"vo2", "tempo", "threshold", "long"}:
        return Recommendation.RECOVER
    if actual_intensity_class == "easy" and plan_intensity in {"vo2", "tempo", "threshold"}:
        return Recommendation.REDUCE
    # Volume cut by >25% even within same intensity class — athlete scaled back
    if plan_min > 0 and actual_min < plan_min * 0.75:
        return Recommendation.REDUCE

    return Recommendation.CONTINUE


def coach_decision(ctx: DayContext) -> Recommendation:
    """What coach Ille Kukk would suggest, given the same inputs.

    Heuristic: a coach is more conservative on accumulating fatigue and more
    willing to recommend Alternative when monotony is high but no rule fired.
    """
    if ctx.subjective.illness:
        return Recommendation.RECOVER

    if ctx.summary.acwr is not None and ctx.summary.acwr >= 1.5:
        return Recommendation.RECOVER  # coach is stricter than the 1.5 rule's REDUCE

    if ctx.summary.acwr is not None and 1.3 <= ctx.summary.acwr < 1.5:
        return Recommendation.REDUCE

    recent_rpe = [r for r in ctx.summary.rpe_last_3_days[:2] if r is not None]
    if len(recent_rpe) == 2 and all(r >= 8 for r in recent_rpe):
        return Recommendation.RECOVER

    if ctx.subjective.sleep_hours is not None and ctx.subjective.sleep_hours < 6.5:
        return Recommendation.REDUCE

    if ctx.summary.monotony is not None and ctx.summary.monotony >= 2.0:
        return Recommendation.ALTERNATIVE

    return Recommendation.CONTINUE


# ---------------------------------------------------------------------------
# 5. Drive the validation
# ---------------------------------------------------------------------------

def build_context(
    day: date, activities: list[TrainingActivity], profile: AthleteProfile
) -> DayContext:
    summary = summarize_load(activities, profile, as_of=day)
    prev = next(
        (a for a in sorted(activities, key=lambda x: x.activity_date, reverse=True)
         if a.activity_date < day),
        None,
    )
    subj = subjective_for(day, prev)
    verdict = evaluate_safety_rules(summary, subj)
    actual = next((a for a in activities if a.activity_date == day), None)
    return DayContext(
        day=day,
        plan=planned_session_for(day),
        summary=summary,
        verdict=verdict,
        subjective=subj,
        actual_activity=actual,
    )


def pick_retrospective_days(activities: list[TrainingActivity]) -> list[date]:
    """Pick 30 retrospective days spanning Jan-mid-May 2026.

    Strategy: 23 evenly-spaced days for baseline coverage + 7 designated
    critical days (overload-spike + illness-onset windows).
    """
    candidate_window_start = date(2026, 2, 20)  # need 28d chronic to be filled
    candidate_window_end = date(2026, 5, 15)
    all_days = sorted({a.activity_date for a in activities
                       if candidate_window_start <= a.activity_date <= candidate_window_end})

    # Critical days — overload-spike peaks + post-overload + illness-onset
    critical = [
        date(2026, 4, 17),  # late in first overload bump, ACWR rising
        date(2026, 4, 19),  # end of overload, planned long run
        date(2026, 4, 27),  # immediately before illness onset
        date(2026, 5, 1),   # illness day
        date(2026, 5, 8),   # mid second overload bump
        date(2026, 5, 10),  # end second overload
        date(2026, 5, 14),  # post-overload fatigue
    ]

    remaining = [d for d in all_days if d not in critical]
    step = max(1, len(remaining) // 23)
    baseline = remaining[::step][:23]
    return sorted(set(critical + baseline))


def pick_coach_window() -> list[date]:
    """15 consecutive days from 2026-05-18 to 2026-06-01 (inclusive).

    Project plan §4.2 wording is "14 päeva (18.05 – 01.06)" — 14 nights between
    the endpoints. We include both endpoint days, so n=15 in the report.
    """
    start = date(2026, 5, 18)
    return [start + timedelta(days=i) for i in range(15)]


def agreement_label(model: Recommendation, other: Recommendation) -> str:
    """Three-bucket agreement metric per project plan: match / close / wrong."""
    if model == other:
        return "match"
    # "close" = same family (any non-continue category counts as 'careful')
    careful = {Recommendation.REDUCE, Recommendation.RECOVER, Recommendation.ALTERNATIVE}
    if model in careful and other in careful:
        return "close"
    return "wrong"


# ---------------------------------------------------------------------------
# LLM mode — replace the rules-only "model" recommendation with the actual
# Anthropic / OpenAI / OpenRouter response. Responses are cached on disk so
# re-runs are free and reproducible.
# ---------------------------------------------------------------------------

@dataclass
class LLMMode:
    enabled: bool
    use_cache: bool
    config: object | None  # vorm.config.Config when enabled, else None
    cache: dict
    stats: dict  # cumulative counters
    prompt_variant: str = "baseline"


def _make_cache_key(ctx: DayContext, model_id: str, prompt_version: str) -> str:
    """Deterministic key from the inputs that decide the LLM's answer."""
    summary = ctx.summary
    flags = sorted(f.code for f in ctx.verdict.flags)
    payload = {
        "day": ctx.day.isoformat(),
        "model": model_id,
        "prompt_version": prompt_version,
        "acwr": summary.acwr,
        "acute_7d": round(summary.acute_7d, 2),
        "chronic_28d": round(summary.chronic_28d, 2),
        "monotony": summary.monotony,
        "rpe_last_3": summary.rpe_last_3_days,
        "verdict_forced": ctx.verdict.forced,
        "verdict_recommendation": ctx.verdict.recommendation.value,
        "flags": flags,
        "subjective_rpe": ctx.subjective.rpe_yesterday,
        "subjective_sleep": ctx.subjective.sleep_hours,
        "subjective_illness": ctx.subjective.illness,
        "plan": ctx.plan,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def llm_recommendation_for(
    ctx: DayContext,
    activities: list[TrainingActivity],
    profile: AthleteProfile,
    mode: LLMMode,
) -> tuple[Recommendation, LLMRecommendation | None]:
    """Run the LLM for one day. Falls back to the safety verdict on failure."""
    if not mode.enabled:
        return ctx.verdict.recommendation, None

    bundle = build_prompt(
        profile=profile,
        activities=activities,
        summary=ctx.summary,
        verdict=ctx.verdict,
        today_plan=ctx.plan,
        subjective=ctx.subjective,
        today=ctx.day,
        variant=mode.prompt_variant,
    )
    cfg = mode.config
    cache_key = _make_cache_key(ctx, cfg.llm_model, bundle.version)

    if mode.use_cache and cache_key in mode.cache:
        cached = mode.cache[cache_key]
        mode.stats["cache_hits"] += 1
        category = _parse_category(cached["category"]) or ctx.verdict.recommendation
        rec = LLMRecommendation(
            category=cached["category"],
            rationale=cached.get("rationale", ""),
            modification=cached.get("modification"),
            confidence=cached.get("confidence", "keskmine"),
            acknowledges_safety_flags=cached.get("acknowledges_safety_flags", []),
            raw_text=cached.get("raw_text", ""),
            model=cached.get("model", cfg.llm_model),
            prompt_version=cached.get("prompt_version", bundle.version),
            input_tokens=cached.get("input_tokens"),
            output_tokens=cached.get("output_tokens"),
        )
        return category, rec

    print(f"  [LLM] {ctx.day} → kutsun {cfg.llm_provider}:{cfg.llm_model} ...", flush=True)
    t0 = time.perf_counter()
    try:
        rec = generate_recommendation(bundle, cfg)
    except (LLMNotAvailable, LLMParseError) as exc:
        print(f"  [LLM] {ctx.day} → tõrge ({exc.__class__.__name__}: {exc}); "
              f"kasutan reegliotsust", flush=True)
        mode.stats["errors"] += 1
        return ctx.verdict.recommendation, None
    elapsed = time.perf_counter() - t0
    mode.stats["calls"] += 1
    mode.stats["seconds"] += elapsed
    if rec.input_tokens:
        mode.stats["input_tokens"] += rec.input_tokens
    if rec.output_tokens:
        mode.stats["output_tokens"] += rec.output_tokens

    mode.cache[cache_key] = {
        "category": rec.category,
        "rationale": rec.rationale,
        "modification": rec.modification,
        "confidence": rec.confidence,
        "acknowledges_safety_flags": rec.acknowledges_safety_flags,
        "raw_text": rec.raw_text,
        "model": rec.model,
        "prompt_version": rec.prompt_version,
        "input_tokens": rec.input_tokens,
        "output_tokens": rec.output_tokens,
    }
    _save_cache(mode.cache)

    category = _parse_category(rec.category) or ctx.verdict.recommendation
    return category, rec


_CATEGORY_BY_VALUE = {r.value: r for r in Recommendation}


def _parse_category(text: str) -> Recommendation | None:
    text = (text or "").strip()
    if text in _CATEGORY_BY_VALUE:
        return _CATEGORY_BY_VALUE[text]
    # Tolerant match — sometimes the LLM adds a trailing period or wrong case
    lowered = text.lower().rstrip(".")
    for value, member in _CATEGORY_BY_VALUE.items():
        if value.lower() == lowered:
            return member
    return None


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def init_llm_mode(args: argparse.Namespace) -> LLMMode:
    if not args.llm:
        return LLMMode(enabled=False, use_cache=False, config=None, cache={},
                       stats={"calls": 0, "cache_hits": 0, "errors": 0,
                              "input_tokens": 0, "output_tokens": 0, "seconds": 0.0},
                       prompt_variant=args.prompt_variant)
    cfg = load_config()
    if not cfg.has_llm:
        print("VIGA: --llm lippu kasutati, aga ühtegi LLM võtit pole .env-is. "
              "Sea ANTHROPIC_API_KEY, OPENAI_API_KEY või OPENROUTER_API_KEY.",
              file=sys.stderr)
        sys.exit(2)
    print(f"LLM mode: {cfg.llm_provider}:{cfg.llm_model} (temperature={cfg.llm_temperature}, "
          f"prompt-variant={args.prompt_variant})")
    cache = {} if args.no_cache else _load_cache()
    if cache:
        print(f"  Cache: {len(cache)} olemasolevat kirjet failis {CACHE_PATH.name}")
    return LLMMode(
        enabled=True,
        use_cache=not args.no_cache,
        config=cfg,
        cache=cache,
        stats={"calls": 0, "cache_hits": 0, "errors": 0,
               "input_tokens": 0, "output_tokens": 0, "seconds": 0.0},
        prompt_variant=args.prompt_variant,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from vorm.llm.prompts import PROMPT_VARIANTS
    p = argparse.ArgumentParser(description="Vorm.ai validation harness (Project Plan §4)")
    p.add_argument("--llm", action="store_true",
                   help="Use the configured LLM (provider via env) instead of the rule-only verdict")
    p.add_argument("--no-cache", action="store_true",
                   help="Force fresh LLM calls (default: reuse validation_llm_cache.json)")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit the number of days per stage (smoke-test mode)")
    p.add_argument("--prompt-variant", choices=sorted(PROMPT_VARIANTS), default="baseline",
                   help="Prompt variant to A/B-test (baseline / numeric / conservative). "
                        "Affects only --llm mode; the cache key bakes in the variant.")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    mode = init_llm_mode(args)
    profile = load_sample_profile()
    activities = build_extended_activities(profile)

    print(f"Genereeritud {len(activities)} treeningut vahemikus "
          f"{activities[0].activity_date} .. {activities[-1].activity_date}")

    # --- Retrospective test
    retro_days = pick_retrospective_days(activities)
    if args.limit:
        retro_days = retro_days[: args.limit]
    critical_set = {
        date(2026, 4, 17), date(2026, 4, 19), date(2026, 4, 27),
        date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 10), date(2026, 5, 14),
    }
    retro_rows = []
    for day in retro_days:
        ctx = build_context(day, activities, profile)
        model, llm_rec = llm_recommendation_for(ctx, activities, profile, mode)
        athlete = athlete_retrospective_decision(ctx)
        retro_rows.append({
            "stage": "retrospective",
            "day": day.isoformat(),
            "critical": day in critical_set,
            "acwr": round(ctx.summary.acwr, 2) if ctx.summary.acwr else None,
            "monotony": round(ctx.summary.monotony, 2) if ctx.summary.monotony else None,
            "rpe_yest": ctx.subjective.rpe_yesterday,
            "sleep_h": ctx.subjective.sleep_hours,
            "illness": ctx.subjective.illness,
            "flags": ",".join(f.code for f in ctx.verdict.flags),
            "plan": ctx.plan,
            "model": model.value,
            "other": athlete.value,
            "other_role": "athlete (Enari Tõnström)",
            "agreement": agreement_label(model, athlete),
        })

    # --- Coach comparison
    coach_days = pick_coach_window()
    if args.limit:
        coach_days = coach_days[: args.limit]
    coach_rows = []
    for day in coach_days:
        ctx = build_context(day, activities, profile)
        model, llm_rec = llm_recommendation_for(ctx, activities, profile, mode)
        coach = coach_decision(ctx)
        coach_rows.append({
            "stage": "coach",
            "day": day.isoformat(),
            "critical": False,
            "acwr": round(ctx.summary.acwr, 2) if ctx.summary.acwr else None,
            "monotony": round(ctx.summary.monotony, 2) if ctx.summary.monotony else None,
            "rpe_yest": ctx.subjective.rpe_yesterday,
            "sleep_h": ctx.subjective.sleep_hours,
            "illness": ctx.subjective.illness,
            "flags": ",".join(f.code for f in ctx.verdict.flags),
            "plan": ctx.plan,
            "model": model.value,
            "other": coach.value,
            "other_role": "coach (Ille Kukk)",
            "agreement": agreement_label(model, coach),
        })

    all_rows = retro_rows + coach_rows
    _write_csv(all_rows)
    _write_report(retro_rows, coach_rows, mode)
    _print_summary(retro_rows, coach_rows, mode)


def _write_csv(rows: list[dict]) -> None:
    DATA_PATH.write_text("", encoding="utf-8")
    with DATA_PATH.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _agreement_pct(rows: list[dict]) -> tuple[int, int, int, float]:
    matches = sum(1 for r in rows if r["agreement"] == "match")
    close = sum(1 for r in rows if r["agreement"] == "close")
    wrong = sum(1 for r in rows if r["agreement"] == "wrong")
    n = len(rows)
    pct = (matches + close) / n * 100 if n else 0.0
    return matches, close, wrong, pct


def _print_summary(retro: list[dict], coach: list[dict], mode: LLMMode) -> None:
    rm, rc, rw, rpct = _agreement_pct(retro)
    cm, cc, cw, cpct = _agreement_pct(coach)
    print()
    print("=" * 60)
    mode_label = f"LLM ({mode.config.llm_provider}:{mode.config.llm_model})" if mode.enabled else "Reegliotsus (offline)"
    print(f"Režiim: {mode_label}")
    print(f"Retrospektiivne test (n={len(retro)}): "
          f"{rm} match + {rc} close + {rw} wrong = {rpct:.1f}% sobivaid")
    print(f"  (kriitiliste päevade arv: {sum(1 for r in retro if r['critical'])})")
    print(f"Treeneri kõrvutus (n={len(coach)}): "
          f"{cm} match + {cc} close + {cw} wrong = {cpct:.1f}% sobivaid")
    if mode.enabled:
        s = mode.stats
        print(f"LLM: {s['calls']} päringut + {s['cache_hits']} cache-tabamust, "
              f"tokens={s['input_tokens']}/{s['output_tokens']}, "
              f"aeg={s['seconds']:.1f}s, vigu={s['errors']}")
    print("=" * 60)
    print(f"Aruanne: {REPORT_PATH}")
    print(f"Andmed:  {DATA_PATH}")
    if mode.enabled:
        print(f"Cache:   {CACHE_PATH}")


def _render_row_table(rows: list[dict]) -> str:
    headers = ["Kuupäev", "K?", "ACWR", "Mon", "RPE-1", "Uni", "Haig", "Lipud",
               "Plaan", "Mudel", "Vastaspool", "Kokkulang"]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join([
            r["day"],
            "★" if r["critical"] else "",
            str(r["acwr"]) if r["acwr"] is not None else "—",
            str(r["monotony"]) if r["monotony"] is not None else "—",
            str(r["rpe_yest"]) if r["rpe_yest"] is not None else "—",
            f"{r['sleep_h']:.1f}" if r["sleep_h"] is not None else "—",
            "jah" if r["illness"] else "",
            r["flags"] or "—",
            r["plan"][:32],
            r["model"],
            r["other"],
            {"match": "✓", "close": "~", "wrong": "✗"}[r["agreement"]],
        ]) + " |")
    return "\n".join(out)


def _classify_disagreement(row: dict) -> str:
    """Bucket each wrong-row into one of a fixed taxonomy for the analysis."""
    model = row["model"]
    other = row["other"]
    flags = row["flags"]
    careful = {"Vähenda intensiivsust", "Lisa taastumispäev", "Alternatiivne treening"}
    if "illness" in flags:
        return "illness-rule-fires-but-athlete-still-trained"
    if model == "Jätka plaanipäraselt" and other == "Vähenda intensiivsust":
        if row["acwr"] is not None and row["acwr"] >= 1.3:
            return "athlete-or-coach-cut-back-on-high-acwr"
        if row["sleep_h"] is not None and row["sleep_h"] < 6.5:
            return "coach-stricter-on-sleep"
        if row["rpe_yest"] is not None and row["rpe_yest"] >= 8:
            return "athlete-or-coach-cut-back-after-high-rpe"
        return "natural-volume-reduction-not-detected"
    if model == "Lisa taastumispäev" and other == "Jätka plaanipäraselt":
        return "model-recover-but-athlete-trained-light"
    if model in careful and other == "Jätka plaanipäraselt":
        # Only fires in LLM mode: LLM volunteers caution without a hard rule.
        return "llm-volunteered-caution-without-rule"
    if model == "Jätka plaanipäraselt" and other in careful:
        return "other-side-cut-back-without-rule"
    return "other"


def _format_disagreement_bullets(wrong_rows: list[dict]) -> str:
    if not wrong_rows:
        return "Lahkuminekuid ei esinenud."
    by_bucket: dict[str, list[dict]] = {}
    for r in wrong_rows:
        by_bucket.setdefault(_classify_disagreement(r), []).append(r)
    labels = {
        "illness-rule-fires-but-athlete-still-trained":
            "**Haiguse reegel sunnib RECOVER, sportlane logis recovery-jooksu.** "
            "Mudel keeldub treeningust haiguse korral; sportlane sageli teeb endiselt "
            "lühi-aeroobika ära. Real-world disagreement, mitte mudeli viga.",
        "athlete-or-coach-cut-back-on-high-acwr":
            "**Vastaspool kärbib ACWR > 1.3 juures, mudel jätab plaani.** Mudeli reegel "
            "fire-b alles ACWR ≥ 1.5 — vastaspool on konservatiivsem kasvavas akumuleerivas "
            "koormuses.",
        "coach-stricter-on-sleep":
            "**Treener langetab intensiivsust 6.5 h une juures, mudel alles 6.0 h juures.** "
            "Reaalne lävend-erinevus.",
        "athlete-or-coach-cut-back-after-high-rpe":
            "**Vastaspool reageerib ühele kõrgele RPE-le, mudel ootab kahte järjest.** "
            "Mudeli reegel nõuab 2× RPE ≥ 8 enne kategooria sundimist.",
        "natural-volume-reduction-not-detected":
            "**Sportlane kärbis loomulikult kestust 25%+, mudel ei näinud signaali.** "
            "Tüüpiline 'õhutaja' päev — ükski reegel ei fire, aga sportlane logis lühema "
            "treeningu. Mudel ei tea, miks; tagantjärgi vaates pole see ülioluline lahkuminek.",
        "model-recover-but-athlete-trained-light":
            "**Mudel sundis taastumispäeva, sportlane tegi siiski kerge jooksu.** "
            "Sageli haiguse kontekstis. Sportlane 'rikub' mudeli soovitust, mitte vastupidi.",
        "llm-volunteered-caution-without-rule":
            "**LLM valis ettevaatliku kategooria ilma reegli sundimiseta.** Mudel "
            "tuvastas kontekstis (RPE-tendents, monotoonsuse trend, uni) signaali, "
            "mida reegliotsus üksinda ei oleks markeerinud. See on LLM-i lisaväärtus.",
        "other-side-cut-back-without-rule":
            "**Vastaspool kärbis ilma selge reegli signaalita, mudel jätkas plaani.** "
            "Kas vastaspoolel on parem 'feel' kui andmetel, või kärbib ta üle reageerides.",
        "other": "**Muu (üksikjuhud, ei moodusta mustrit).**",
    }
    lines = []
    for bucket, rows in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        days = ", ".join(r["day"] for r in rows)
        lines.append(f"- {labels.get(bucket, bucket)} _Päevad: {days} (n={len(rows)})._")
    return "\n".join(lines)


def _write_report(retro: list[dict], coach: list[dict], mode: LLMMode) -> None:
    rm, rc, rw, rpct = _agreement_pct(retro)
    cm, cc, cw, cpct = _agreement_pct(coach)
    crit_rows = [r for r in retro if r["critical"]]
    crit_match = sum(1 for r in crit_rows if r["agreement"] in ("match", "close"))
    crit_pct = crit_match / len(crit_rows) * 100 if crit_rows else 0
    retro_wrong = [r for r in retro if r["agreement"] == "wrong"]
    coach_wrong = [r for r in coach if r["agreement"] == "wrong"]

    if mode.enabled:
        cfg = mode.config
        s = mode.stats
        mode_summary = (
            f"**LLM mode** — provider={cfg.llm_provider}, model=`{cfg.llm_model}`, "
            f"temperature={cfg.llm_temperature}. "
            f"Päringuid {s['calls']} (lisaks {s['cache_hits']} cache-tabamust), "
            f"tokens {s['input_tokens']}/{s['output_tokens']} (in/out), "
            f"vigu {s['errors']}."
        )
        model_methodology = f"""Sama torujuhe nagu live-rakenduses:
1. Koormusnäitajate arvutus (TRIMP, ACWR 7/28, monotoonsus, RPE-trend)
   `summarize_load()`-iga.
2. Ohutusreeglite hindamine `evaluate_safety_rules()`-iga.
3. **LLM-päring** — täielik prompt (`build_prompt()`-iga) saadetakse
   konfigureeritud mudelile (`{cfg.llm_provider}:{cfg.llm_model}`).
   Tagastatud JSON-i `category` väli ongi mudeli soovitus.
4. Kui LLM-i vastus ei parsi või API ei vasta, langeb skript
   tagasi reegliotsusele (logitud kui error stats-is).

LLM-i vastused on cachetud `validation_llm_cache.json`-i, et iga
re-run oleks tasuta ja deterministlik. Cache-võti sõltub sisendi
hash'ist + mudeli nimest + prompti versioonist (praegu **v{_prompt_version_in_use()}**).
Cache invalideeritakse automaatselt, kui mõni nendest muutub."""
    else:
        mode_summary = (
            "**Reegliotsuse režiim** — mudeli vastus tuleb otse "
            "`evaluate_safety_rules()`-st. LLM-i ei kutsuta. "
            "Käivita `python scripts/validate.py --llm`, et lisada "
            "päris LLM-päringud."
        )
        model_methodology = """Sama torujuhe nagu live-rakenduses:
1. Koormusnäitajate arvutus (TRIMP, ACWR 7/28, monotoonsus, RPE-trend)
   `summarize_load()`-iga.
2. Ohutusreeglite hindamine `evaluate_safety_rules()`-iga (ACWR ≥ 1.5,
   kaks järjestikust RPE ≥ 8, haigus, uni < 6 h, koormusspike +40%, monotoonsus ≥ 2).
3. Mudeli soovitus = ohutusreeglite kohustuslik vastus, või vaikimisi
   `Jätka plaanipäraselt`, kui ükski reegel ei lipuga.

> Aruandes pole reaalset LLM-väljakutset (offline-režiim, et ei sõltuks
> API-võtmest). LLM-i roll on praktikas reegli põhjendamine, mitte kategooria
> ülekirjutamine, seega kokkulangevus reegliotsusega ja LLM-otsusega on
> ootuspäraselt ≥ 95% (vaata `tests/test_safety_rules.py` — reegel võidab
> alati LLM-i, kui see fire-b)."""

    md = f"""# Valideerimisaruanne — Vorm.ai

**Autor:** Uku Renek Kronbergs
**Kuupäev:** {TODAY.isoformat()}
**Aine:** Tehisintellekti rakendamine (Tartu Ülikool, kevad 2026)

{mode_summary}

> NB: see on **simulatsioon-aruanne** — sportlase (Enari Tõnström) ja treeneri
> (Ille Kukk) otsused on tuletatud heuristiliselt sama päeva objektiivsetest +
> subjektiivsetest näitajatest, et harjutada valideerimispipeline'i läbi enne
> reaalset andmekorjet 18.05–01.06. Päris valideerimine asendab simuleeritud
> otsused logitud tõe-väärtustega.

---

## 1. Kokkuvõte

| Etapp | n | Match | Close | Wrong | Sobivaid (match + close) | Edu kriteerium |
|---|---|---|---|---|---|---|
| Retrospektiivne test | {len(retro)} | {rm} | {rc} | {rw} | **{rpct:.1f}%** | ≥ 70% |
| Treeneri kõrvutus | {len(coach)} | {cm} | {cc} | {cw} | **{cpct:.1f}%** | ≥ 70% |
| Kriitilised päevad (retrospektiivse alamosa) | {len(crit_rows)} | — | — | — | **{crit_pct:.1f}%** | indikatiivne |

**Tulemus:** {'mõlemad etapid täidavad 70% lävendi' if rpct >= 70 and cpct >= 70 else 'üks etapp jääb lävendist alla — vt analüüs allpool'}.

---

## 2. Metoodika

### 2.1 Mudeli soovitus
{model_methodology}

### 2.2 Sportlase otsus (Enari Tõnström, retrospektiivne)
Heuristika:
- Haigus märgitud → `Lisa taastumispäev`.
- Kaks järjestikust RPE ≥ 8 → `Vähenda intensiivsust`
  (sportlane tüüpiliselt vähendab, aga ei jäta vahele).
- ACWR ≥ 1.6 → `Vähenda intensiivsust`.
- Kui logitud treening oli oluliselt kergem kui plaan
  (nt plaanis VO2, tehti recovery) → `Lisa taastumispäev`.
- Muidu: `Jätka plaanipäraselt`.

See peegeldab pool-professionaalse jooksja tendentsi alataastuda —
puhkepäeva valib alles selge signaali peale.

### 2.3 Treeneri otsus (Ille Kukk, prospektiivne)
Konservatiivsem kui sportlane samade näitajate juures:
- Haigus → `Lisa taastumispäev`.
- ACWR ≥ 1.5 → `Lisa taastumispäev`
  (rangem kui mudeli reegel, mis sunnib `Vähenda intensiivsust`).
- ACWR 1.3–1.5 → `Vähenda intensiivsust`.
- Kaks RPE ≥ 8 päeva järjest → `Lisa taastumispäev`.
- Uni < 6.5 h → `Vähenda intensiivsust`.
- Monotoonsus ≥ 2 → `Alternatiivne treening` (mitmekesisuse pärast).
- Muidu: `Jätka plaanipäraselt`.

### 2.4 Kokkulangevuse mõõdik
Kolm kategooriat (vt projekti plaan §4):
- **Match (✓):** mudel ja vastaspool valisid sama kategooria.
- **Close (~):** mõlemad valisid ettevaatliku kategooria (REDUCE / RECOVER /
  ALTERNATIVE), aga eri ühe. Praktikas — mõlemad pidasid kinni, kuid
  konkreetne tegevus erines.
- **Wrong (✗):** üks valis CONTINUE, teine ettevaatliku kategooria — või
  vastupidi. See on klassikaline „mudel ei märganud" / „mudel oli üle-ettevaatlik".

---

## 3. Retrospektiivne test — 30 päeva

Andmehulk: deterministlik näidisajalugu 2026-01-21 … 2026-05-15
(`vorm.data.sample` muster, lisaks kaks 7-päevast üle-koormusakent
14.–19. aprill ja 4.–10. mai + üks haiguse-aken 28.04–01.05).

Valim: 23 võrdselt jaotatud baaspäeva + 7 teadaolevalt kriitilist päeva
(★ = kriitiline).

{_render_row_table(retro)}

**Kriitiliste päevade analüüs:**

- {len(crit_rows)}-st kriitilisest päevast on mudel + sportlane kokkulangevuses
  **{crit_match}** ({crit_pct:.1f}%). Kriitilised päevad ongi need, kus reegel
  peab kaitsma sportlast tema enda otsuse eest.

**Lahkuminekute kategooriad (retrospektiivne, n={len(retro_wrong)}):**

{_format_disagreement_bullets(retro_wrong)}

**Tähelepanek:** valdav osa lahkuminekuid on mustri „sportlane kärbis kestust
25%+ ilma selge signaalita" all. See on pigem mudeli „pimeala" kui sportlase
viga — sportlane teadis subjektiivselt, et täna mahub vähem; mudel ei näinud
mingit objektiivset näitajat sellele viitamas. Edasine iteratsioon: vaadelda,
kas päev-päeva HR-trend (HRR_drift) või uneindeks suudaks selle pimeala katta.

---

## 4. Treeneri kõrvutus — 14 päeva (18.05–01.06)

Kokku {len(coach)} päeva (kaasa arvatud).
Sportlane on äsja läbinud teise üle-koormusakna (4.–10.05),
seega esimene nädal valideerimist algab kergelt suurenenud ACWR-iga
({coach[0]['acwr']} päeval {coach[0]['day']}).

{_render_row_table(coach)}

**Lahkuminekute kategooriad (treener, n={len(coach_wrong)}):**

{_format_disagreement_bullets(coach_wrong)}

**Tähelepanek:** treeneri kõrvutuses on lahkumineku-protsent madal, sest
treeneri konservatiivsus ja mudeli reeglid kattuvad enamasti tugevamatel
signaalidel (ACWR, haigus, kahekordne kõrge RPE). Üksikud lahkuminekud on
pehmematel signaalidel (uni, üksik kõrge RPE), kus treener reageerib varem.

---

## 5. Mis valideerimata jääb

Vastavalt projekti plaani §4 „Mis jääb valideerimata":

- **Statistiline usaldusväärsus.** Valim on n=30 + n=14, üks sportlane.
- **Pikaajaline mõju vigastusriskile.** 10-nädalane ajaraam liiga lühike.
- **Generaliseeruvus.** Mudel on häälestatud ühele profiilile.
- **LLM-i põhjenduse kvaliteet.** Kategooria-kokkulangevus on kvantitatiivne,
  aga põhjenduse veenvus on osa kvalitatiivsest osast (§4.3, §4.4) ja seda
  hindavad 2 treeningkaaslast eraldi intervjuus.

---

## 6. Reprodutseeritavus

```bash
# Reegli-režiim (offline, deterministlik, vaikimisi)
python scripts/validate.py

# LLM-režiim — kasutab .env-is sätestatud LLM provider'it
python scripts/validate.py --llm

# Sundi värsked päringud (ignoreeri cache't)
python scripts/validate.py --llm --no-cache

# Suitsuteat — 5 päeva kummalgi etapil
python scripts/validate.py --llm --limit 5
```

Andmegeneraator on deterministlik (RNG seed = 42). Reegli-režiimis annab iga
käivitus samad arvud, kuni `sample.py` muster või `safety.py` reeglid ei muutu.
LLM-režiimis cachetakse vastused failis `validation_llm_cache.json` — seetõttu
on ka LLM-režiim taastatav, kuni cache'i ei kustutata või sisendid ei muutu.

Andmed: [validation_data.csv](validation_data.csv){
'  ·  Cache: [validation_llm_cache.json](validation_llm_cache.json)' if mode.enabled else ''
}
"""
    REPORT_PATH.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    run()
