from __future__ import annotations

from enum import Enum
from typing import Any


class EscalationLevel(str, Enum):
    L0_MONITOR = "L0_MONITOR"
    L1_NURSING_REVIEW = "L1_NURSING_REVIEW"
    L2_URGENT_PROVIDER_REVIEW = "L2_URGENT_PROVIDER_REVIEW"
    L3_RAPID_RESPONSE_REVIEW = "L3_RAPID_RESPONSE_REVIEW"
    L4_EMERGENCY_RESPONSE = "L4_EMERGENCY_RESPONSE"


ORDER: tuple[EscalationLevel, ...] = (
    EscalationLevel.L0_MONITOR,
    EscalationLevel.L1_NURSING_REVIEW,
    EscalationLevel.L2_URGENT_PROVIDER_REVIEW,
    EscalationLevel.L3_RAPID_RESPONSE_REVIEW,
    EscalationLevel.L4_EMERGENCY_RESPONSE,
)

LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.L0_MONITOR: "Monitor",
    EscalationLevel.L1_NURSING_REVIEW: "Nursing Review",
    EscalationLevel.L2_URGENT_PROVIDER_REVIEW: "Urgent Provider Review",
    EscalationLevel.L3_RAPID_RESPONSE_REVIEW: "Rapid Response Review",
    EscalationLevel.L4_EMERGENCY_RESPONSE: "Emergency Response",
}

ROLES: dict[EscalationLevel, str] = {
    EscalationLevel.L0_MONITOR: "Monitoring",
    EscalationLevel.L1_NURSING_REVIEW: "Nursing Review",
    EscalationLevel.L2_URGENT_PROVIDER_REVIEW: "Provider Review",
    EscalationLevel.L3_RAPID_RESPONSE_REVIEW: "Rapid Response Team",
    EscalationLevel.L4_EMERGENCY_RESPONSE: "Emergency Response",
}

_ALIASES = {
    "L0": EscalationLevel.L0_MONITOR,
    "MONITOR": EscalationLevel.L0_MONITOR,
    "L1": EscalationLevel.L1_NURSING_REVIEW,
    "NURSING": EscalationLevel.L1_NURSING_REVIEW,
    "NURSING_REVIEW": EscalationLevel.L1_NURSING_REVIEW,
    "L2": EscalationLevel.L2_URGENT_PROVIDER_REVIEW,
    "PROVIDER": EscalationLevel.L2_URGENT_PROVIDER_REVIEW,
    "URGENT_PROVIDER_REVIEW": EscalationLevel.L2_URGENT_PROVIDER_REVIEW,
    "L3": EscalationLevel.L3_RAPID_RESPONSE_REVIEW,
    "RRT": EscalationLevel.L3_RAPID_RESPONSE_REVIEW,
    "RAPID_RESPONSE": EscalationLevel.L3_RAPID_RESPONSE_REVIEW,
    "RAPID_RESPONSE_REVIEW": EscalationLevel.L3_RAPID_RESPONSE_REVIEW,
    "L4": EscalationLevel.L4_EMERGENCY_RESPONSE,
    "EMERGENCY": EscalationLevel.L4_EMERGENCY_RESPONSE,
    "EMERGENCY_RESPONSE": EscalationLevel.L4_EMERGENCY_RESPONSE,
}


def normalize_level(value: Any, *, default: EscalationLevel | None = None) -> EscalationLevel:
    if isinstance(value, EscalationLevel):
        return value
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not text:
        if default is not None:
            return default
        raise ValueError("Escalation level is required.")
    try:
        return EscalationLevel(text)
    except ValueError:
        if text in _ALIASES:
            return _ALIASES[text]
        if default is not None:
            return default
        raise ValueError(f"Unsupported escalation level: {value!r}")


def level_rank(value: Any) -> int:
    return ORDER.index(normalize_level(value))


def level_label(value: Any) -> str:
    return LABELS[normalize_level(value)]


def level_role(value: Any) -> str:
    return ROLES[normalize_level(value)]


def max_level(*values: Any) -> EscalationLevel:
    levels = [normalize_level(value) for value in values]
    if not levels:
        return EscalationLevel.L0_MONITOR
    return max(levels, key=level_rank)


def next_level(value: Any) -> EscalationLevel:
    current = normalize_level(value)
    index = level_rank(current)
    return ORDER[min(index + 1, len(ORDER) - 1)]


def is_terminal_level(value: Any) -> bool:
    return normalize_level(value) == EscalationLevel.L4_EMERGENCY_RESPONSE
