from __future__ import annotations

import json
import os
from typing import Any

from app.escalation.levels import EscalationLevel, legacy_level_code, level_role, normalize_level


_ENV_KEYS: dict[EscalationLevel, tuple[str, ...]] = {
    EscalationLevel.CARE_TEAM_REVIEW: ("ESCALATION_EMAIL_CARE_TEAM", "ESCALATION_EMAIL_L1"),
    EscalationLevel.URGENT_PROVIDER_REVIEW: ("ESCALATION_EMAIL_URGENT_PROVIDER", "ESCALATION_EMAIL_L2"),
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: ("ESCALATION_EMAIL_RRT", "ESCALATION_EMAIL_L3"),
    EscalationLevel.CODE_RESPONSE_ACTIVATION: ("ESCALATION_EMAIL_CODE", "ESCALATION_EMAIL_L4"),
}


class RecipientDirectory:
    def _json_directory(self) -> dict[str, Any]:
        raw = os.getenv("ESCALATION_EMAIL_RECIPIENTS_JSON", "{}").strip() or "{}"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def resolve(self, level: EscalationLevel | str) -> dict[str, str] | None:
        normalized = normalize_level(level)
        directory = self._json_directory()
        value = directory.get(normalized.value) or directory.get(legacy_level_code(normalized))
        if isinstance(value, str) and value.strip():
            return {"email": value.strip(), "role": level_role(normalized)}
        if isinstance(value, dict):
            email = str(value.get("email") or "").strip()
            if email:
                return {"email": email, "role": str(value.get("role") or level_role(normalized)).strip()}

        for env_key in _ENV_KEYS.get(normalized, ()):
            email = str(os.getenv(env_key, "") or "").strip()
            if email:
                return {"email": email, "role": level_role(normalized)}
        return None


recipient_directory = RecipientDirectory()
