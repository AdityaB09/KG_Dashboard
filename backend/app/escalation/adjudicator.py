from __future__ import annotations

from app.escalation.levels import EscalationLevel, max_level


def adjudicate(
    *,
    model_suggested_level: EscalationLevel,
    policy_minimum_level: EscalationLevel,
) -> EscalationLevel:
    return max_level(model_suggested_level, policy_minimum_level)
