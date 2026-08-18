from __future__ import annotations

import os
from enum import Enum
from typing import Any


class EscalationLevel(str, Enum):
    """Stable internal response pathways used by CARDINAL.

    V11 keeps the V10 stored values so existing cases, precomputed outputs and
    vendor adapters stay compatible.  The externally displayed normalized tier
    codes are T0/T1/T2/T3/E.  They are CARDINAL's portable hospital-policy
    abstraction patterned on a publicly documented Cerner Rapid Response
    implementation; they are not vendor-defined Cerner/Oracle product levels.
    """

    MONITOR_ONLY = "MONITOR_ONLY"
    CARE_TEAM_REVIEW = "CARE_TEAM_REVIEW"
    URGENT_PROVIDER_REVIEW = "URGENT_PROVIDER_REVIEW"
    RAPID_RESPONSE_ACTIVATION = "RAPID_RESPONSE_ACTIVATION"
    CODE_RESPONSE_ACTIVATION = "CODE_RESPONSE_ACTIVATION"

    # Backward-compatible Python aliases from earlier CARDINAL versions.
    L0_MONITOR = "MONITOR_ONLY"
    L1_NURSING_REVIEW = "CARE_TEAM_REVIEW"
    L2_URGENT_PROVIDER_REVIEW = "URGENT_PROVIDER_REVIEW"
    L3_RAPID_RESPONSE_REVIEW = "RAPID_RESPONSE_ACTIVATION"
    L4_EMERGENCY_RESPONSE = "CODE_RESPONSE_ACTIVATION"


ORDER: tuple[EscalationLevel, ...] = (
    EscalationLevel.MONITOR_ONLY,
    EscalationLevel.CARE_TEAM_REVIEW,
    EscalationLevel.URGENT_PROVIDER_REVIEW,
    EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    EscalationLevel.CODE_RESPONSE_ACTIVATION,
)

LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "Monitor",
    EscalationLevel.CARE_TEAM_REVIEW: "Clinical Review",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "Urgent Review",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "Rapid Response",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "Emergency Override",
}

ROLES: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "Monitoring",
    EscalationLevel.CARE_TEAM_REVIEW: "Assigned Clinical Care Team",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "Responsible Provider / Charge Team",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "Rapid Response Team",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "Emergency / Resuscitation Team",
}

# Portable external tier interface. Emergency is intentionally outside the
# numbered sequence rather than a fabricated T4.
TIER_CODES: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "T0_MONITOR",
    EscalationLevel.CARE_TEAM_REVIEW: "T1_CLINICAL_REVIEW",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "T2_URGENT_REVIEW",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "T3_RAPID_RESPONSE",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "E_EMERGENCY_OVERRIDE",
}

SHORT_LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "T0",
    EscalationLevel.CARE_TEAM_REVIEW: "T1",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "T2",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "T3",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "E",
}

# Qualitative reference bands mirror the four-band severity stratification
# publicly documented at MU Health Care. They are not a NEWS calculation and do
# not imply that CARDINAL reproduces MU's thresholds.
REFERENCE_BANDS: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "LOW",
    EscalationLevel.CARE_TEAM_REVIEW: "MODERATE",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "HIGH",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "VERY_HIGH",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "EMERGENCY_OVERRIDE",
}

# Default Oracle Health Messages adapter semantics. Oracle exposes NORMAL
# (ROUTINE) and HIGH (STAT); it does not expose a universal multi-level clinical
# ladder. Sites may override these four values in backend/.env.
ORACLE_PRIORITY_DEFAULTS: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "NORMAL",
    EscalationLevel.CARE_TEAM_REVIEW: "NORMAL",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "NORMAL",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "HIGH",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "HIGH",
}

ORACLE_PRIORITY_ENV: dict[EscalationLevel, str] = {
    EscalationLevel.CARE_TEAM_REVIEW: "ORACLE_ESCALATION_PRIORITY_T1",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "ORACLE_ESCALATION_PRIORITY_T2",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "ORACLE_ESCALATION_PRIORITY_T3",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "ORACLE_ESCALATION_PRIORITY_E",
}

_ALIASES: dict[str, EscalationLevel] = {
    # V11 normalized tier codes.
    "T0": EscalationLevel.MONITOR_ONLY,
    "T0_MONITOR": EscalationLevel.MONITOR_ONLY,
    "T1": EscalationLevel.CARE_TEAM_REVIEW,
    "T1_CLINICAL_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "T2": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "T2_URGENT_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "T3": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "T3_RAPID_RESPONSE": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "E": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "E_EMERGENCY": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "E_EMERGENCY_OVERRIDE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    # V10 canonical/current aliases.
    "MONITOR": EscalationLevel.MONITOR_ONLY,
    "MONITOR_ONLY": EscalationLevel.MONITOR_ONLY,
    "CARE_TEAM": EscalationLevel.CARE_TEAM_REVIEW,
    "CARE_TEAM_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "BEDSIDE_CARE_TEAM": EscalationLevel.CARE_TEAM_REVIEW,
    "BEDSIDE_CARE_TEAM_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "CLINICAL_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "PROVIDER": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "URGENT_PROVIDER_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "URGENT_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "RRT": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE_REVIEW": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE_ACTIVATION": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "CODE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "CODE_RESPONSE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "CODE_RESPONSE_ACTIVATION": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "EMERGENCY": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "EMERGENCY_RESPONSE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "EMERGENCY_OVERRIDE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    # Historical L0-L4 inputs remain accepted only for migration compatibility.
    "L0": EscalationLevel.MONITOR_ONLY,
    "L0_MONITOR": EscalationLevel.MONITOR_ONLY,
    "L1": EscalationLevel.CARE_TEAM_REVIEW,
    "L1_NURSING_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "NURSING": EscalationLevel.CARE_TEAM_REVIEW,
    "NURSING_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "L2": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "L2_URGENT_PROVIDER_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "L3": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "L3_RAPID_RESPONSE_REVIEW": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "L4": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "L4_EMERGENCY_RESPONSE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
}

LEGACY_CODES: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "L0_MONITOR",
    EscalationLevel.CARE_TEAM_REVIEW: "L1_NURSING_REVIEW",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "L2_URGENT_PROVIDER_REVIEW",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "L3_RAPID_RESPONSE_REVIEW",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "L4_EMERGENCY_RESPONSE",
}


def normalize_level(value: Any, *, default: EscalationLevel | None = None) -> EscalationLevel:
    if isinstance(value, EscalationLevel):
        return value
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not text:
        if default is not None:
            return default
        raise ValueError("Clinical response pathway is required.")
    try:
        return EscalationLevel(text)
    except ValueError:
        if text in _ALIASES:
            return _ALIASES[text]
        if default is not None:
            return default
        raise ValueError(f"Unsupported clinical response pathway: {value!r}")


def level_rank(value: Any) -> int:
    return ORDER.index(normalize_level(value))


def level_label(value: Any) -> str:
    return LABELS[normalize_level(value)]


def level_role(value: Any) -> str:
    return ROLES[normalize_level(value)]


def level_short_label(value: Any) -> str:
    return SHORT_LABELS[normalize_level(value)]


def tier_code(value: Any) -> str:
    return TIER_CODES[normalize_level(value)]


def reference_band(value: Any) -> str:
    return REFERENCE_BANDS[normalize_level(value)]


def oracle_priority(value: Any) -> str:
    normalized = normalize_level(value)
    key = ORACLE_PRIORITY_ENV.get(normalized)
    override = str(os.getenv(key, "") if key else "").strip().upper()
    return override if override in {"HIGH", "NORMAL"} else ORACLE_PRIORITY_DEFAULTS[normalized]


def legacy_level_code(value: Any) -> str:
    return LEGACY_CODES[normalize_level(value)]


def max_level(*values: Any) -> EscalationLevel:
    levels = [normalize_level(value) for value in values]
    if not levels:
        return EscalationLevel.MONITOR_ONLY
    return max(levels, key=level_rank)


def next_level(value: Any) -> EscalationLevel:
    """Advance only through the normal response sequence T0->T1->T2->T3.

    Emergency Override is condition-driven and can be entered from any tier; it
    is intentionally not the timeout successor of Rapid Response.
    """
    current = normalize_level(value)
    if current in {EscalationLevel.RAPID_RESPONSE_ACTIVATION, EscalationLevel.CODE_RESPONSE_ACTIVATION}:
        return current
    index = level_rank(current)
    return ORDER[min(index + 1, ORDER.index(EscalationLevel.RAPID_RESPONSE_ACTIVATION))]


def is_terminal_level(value: Any) -> bool:
    return normalize_level(value) == EscalationLevel.CODE_RESPONSE_ACTIVATION


def is_auto_advance_terminal(value: Any) -> bool:
    return normalize_level(value) in {
        EscalationLevel.RAPID_RESPONSE_ACTIVATION,
        EscalationLevel.CODE_RESPONSE_ACTIVATION,
    }
