"""Rule-based safety filters — Plan B3 in the project plan.

The LLM is the nuanced advisor, but rules catch the obvious red flags
before the model ever sees the prompt. When a rule fires, the LLM is
still invited to explain *why* the light day is being recommended — it
doesn't get to overrule the rule.

Design: each rule is a small pure function that returns a SafetyFlag or
None. `evaluate_safety_rules` aggregates them and picks the most
conservative recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..data.models import DailySubjective
from ..metrics.load import ACWR_DANGER_HIGH, MONOTONY_HIGH, LoadSummary


class Recommendation(StrEnum):
    CONTINUE = "Jätka plaanipäraselt"
    REDUCE = "Vähenda intensiivsust"
    RECOVER = "Lisa taastumispäev"
    ALTERNATIVE = "Alternatiivne treening"


class SafetyFlagSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SafetyFlag:
    code: str
    severity: SafetyFlagSeverity
    message: str
    forced_recommendation: Recommendation | None = None


@dataclass(frozen=True)
class SafetyVerdict:
    recommendation: Recommendation
    flags: list[SafetyFlag] = field(default_factory=list)
    forced: bool = False

    @property
    def critical_flags(self) -> list[SafetyFlag]:
        return [f for f in self.flags if f.severity == SafetyFlagSeverity.CRITICAL]


def _flag_high_acwr(summary: LoadSummary) -> SafetyFlag | None:
    if summary.acwr is None:
        return None
    if summary.acwr >= ACWR_DANGER_HIGH:
        return SafetyFlag(
            code="acwr_high",
            severity=SafetyFlagSeverity.CRITICAL,
            message=(
                f"ACWR = {summary.acwr:.2f} (≥ {ACWR_DANGER_HIGH}) — akuutne koormus "
                "oluliselt üle kroonilise, vigastuste risk tõusnud."
            ),
            forced_recommendation=Recommendation.REDUCE,
        )
    return None


def _flag_consecutive_high_rpe(summary: LoadSummary) -> SafetyFlag | None:
    recent = [r for r in summary.rpe_last_3_days[:2] if r is not None]
    if len(recent) < 2:
        return None
    if all(r >= 8 for r in recent):
        return SafetyFlag(
            code="rpe_consecutive_high",
            severity=SafetyFlagSeverity.CRITICAL,
            message="Kaks päeva järjest RPE ≥ 8 — närvisüsteem vajab taastumist.",
            forced_recommendation=Recommendation.RECOVER,
        )
    return None


def _flag_high_monotony(summary: LoadSummary) -> SafetyFlag | None:
    if summary.monotony is None:
        return None
    if summary.monotony >= MONOTONY_HIGH:
        return SafetyFlag(
            code="monotony_high",
            severity=SafetyFlagSeverity.WARNING,
            message=(
                f"Monotoonsus = {summary.monotony:.2f} (≥ {MONOTONY_HIGH}) — päevad on liiga sarnased, "
                "lisa varieeruvust (puhkepäev või kõvem päev)."
            ),
        )
    return None


def _flag_illness(subjective: DailySubjective | None) -> SafetyFlag | None:
    if subjective and subjective.illness:
        return SafetyFlag(
            code="illness",
            severity=SafetyFlagSeverity.CRITICAL,
            message="Sportlane märkis haiguse — treening ära jätta või asendada kerge jalutuskäiguga.",
            forced_recommendation=Recommendation.RECOVER,
        )
    return None


def _flag_low_sleep(subjective: DailySubjective | None) -> SafetyFlag | None:
    if subjective and subjective.sleep_hours is not None and subjective.sleep_hours < 6.0:
        return SafetyFlag(
            code="sleep_low",
            severity=SafetyFlagSeverity.WARNING,
            message=f"Uni ainult {subjective.sleep_hours:.1f} h — kaalu intensiivsuse vähendamist.",
        )
    return None


def _flag_load_spike(summary: LoadSummary) -> SafetyFlag | None:
    if summary.chronic_28d <= 0:
        return None
    # Compare 7-day running load against the 28-day baseline. >40% spike = yellow flag.
    spike = (summary.acute_7d - summary.chronic_28d) / summary.chronic_28d
    if spike >= 0.4:
        return SafetyFlag(
            code="load_spike",
            severity=SafetyFlagSeverity.WARNING,
            message=f"7-päeva koormus {spike * 100:.0f}% kõrgem kui 28-päeva keskmine.",
        )
    return None


_OBJECTIVE_RULES = (
    _flag_consecutive_high_rpe,
    _flag_high_acwr,
    _flag_high_monotony,
    _flag_load_spike,
)
_SUBJECTIVE_RULES = (
    _flag_illness,
    _flag_low_sleep,
)

_FORCED_ORDER = {
    Recommendation.RECOVER: 3,
    Recommendation.REDUCE: 2,
    Recommendation.ALTERNATIVE: 1,
    Recommendation.CONTINUE: 0,
}


def evaluate_safety_rules(
    summary: LoadSummary,
    subjective: DailySubjective | None = None,
) -> SafetyVerdict:
    """Run every rule and return the most conservative outcome.

    Precedence (strongest first): RECOVER > REDUCE > ALTERNATIVE > CONTINUE.
    """
    flags: list[SafetyFlag] = []
    for rule in _OBJECTIVE_RULES:
        flag = rule(summary)
        if flag:
            flags.append(flag)
    for rule in _SUBJECTIVE_RULES:
        flag = rule(subjective)
        if flag:
            flags.append(flag)

    forced: Recommendation | None = None
    for flag in flags:
        if flag.forced_recommendation and (
            forced is None
            or _FORCED_ORDER[flag.forced_recommendation] > _FORCED_ORDER[forced]
        ):
            forced = flag.forced_recommendation

    if forced is not None:
        return SafetyVerdict(recommendation=forced, flags=flags, forced=True)
    return SafetyVerdict(recommendation=Recommendation.CONTINUE, flags=flags, forced=False)
