from __future__ import annotations

from enum import Enum
from typing import Any


class EscalationLevel(str, Enum):
    """Site-configurable clinical response pathways.

    Oracle Health Millennium does not define a universal numbered L1-L4 clinical
    escalation ladder.  These names represent common hospital response roles and
    are deliberately vendor-neutral while the Oracle adapter maps them to real
    Millennium Message Center recipients/Group Inboxes and STAT/ROUTINE priority.

    The legacy L0-L4 enum member names remain aliases so existing CARDINAL code and
    stored/precomputed outputs can be upgraded without losing compatibility.
    """

    MONITOR_ONLY = "MONITOR_ONLY"
    CARE_TEAM_REVIEW = "CARE_TEAM_REVIEW"
    URGENT_PROVIDER_REVIEW = "URGENT_PROVIDER_REVIEW"
    RAPID_RESPONSE_ACTIVATION = "RAPID_RESPONSE_ACTIVATION"
    CODE_RESPONSE_ACTIVATION = "CODE_RESPONSE_ACTIVATION"

    # Backward-compatible Python aliases. Their .value is the new canonical code.
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
    EscalationLevel.MONITOR_ONLY: "Monitor Only",
    EscalationLevel.CARE_TEAM_REVIEW: "Bedside Care Team Review",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "Urgent Provider Review",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "Rapid Response Activation",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "Code / Emergency Response Activation",
}

ROLES: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "Monitoring",
    EscalationLevel.CARE_TEAM_REVIEW: "Bedside Care Team",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "Responsible Provider",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "Rapid Response Team",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "Code / Emergency Response Team",
}

SHORT_LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "MONITOR",
    EscalationLevel.CARE_TEAM_REVIEW: "CARE TEAM",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "PROVIDER",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "RRT",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "CODE",
}

# Oracle Health Messages exposes HIGH (STAT) and NORMAL (ROUTINE), not a vendor
# defined L1-L4 clinical ladder.  The site policy maps response pathways to those
# native priorities.
ORACLE_PRIORITY: dict[EscalationLevel, str] = {
    EscalationLevel.MONITOR_ONLY: "NORMAL",
    EscalationLevel.CARE_TEAM_REVIEW: "NORMAL",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "NORMAL",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "HIGH",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "HIGH",
}

_ALIASES: dict[str, EscalationLevel] = {
    # Canonical/current aliases.
    "MONITOR": EscalationLevel.MONITOR_ONLY,
    "MONITOR_ONLY": EscalationLevel.MONITOR_ONLY,
    "CARE_TEAM": EscalationLevel.CARE_TEAM_REVIEW,
    "CARE_TEAM_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "BEDSIDE_CARE_TEAM": EscalationLevel.CARE_TEAM_REVIEW,
    "BEDSIDE_CARE_TEAM_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "PROVIDER": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "URGENT_PROVIDER_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "RRT": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE_REVIEW": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "RAPID_RESPONSE_ACTIVATION": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "CODE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "CODE_RESPONSE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "CODE_RESPONSE_ACTIVATION": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "EMERGENCY": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    "EMERGENCY_RESPONSE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
    # Legacy CARDINAL values accepted from stored cases/precomputed model results.
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


def oracle_priority(value: Any) -> str:
    return ORACLE_PRIORITY[normalize_level(value)]


def legacy_level_code(value: Any) -> str:
    return LEGACY_CODES[normalize_level(value)]


def max_level(*values: Any) -> EscalationLevel:
    levels = [normalize_level(value) for value in values]
    if not levels:
        return EscalationLevel.MONITOR_ONLY
    return max(levels, key=level_rank)


def next_level(value: Any) -> EscalationLevel:
    current = normalize_level(value)
    index = level_rank(current)
    return ORDER[min(index + 1, len(ORDER) - 1)]


def is_terminal_level(value: Any) -> bool:
    return normalize_level(value) == EscalationLevel.CODE_RESPONSE_ACTIVATION
