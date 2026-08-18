from __future__ import annotations

import json
import os
from typing import Any

from app.escalation.levels import EscalationLevel, normalize_level


_DEFAULT_ENV_KEYS = {
    EscalationLevel.L1_NURSING_REVIEW: "ESCALATION_EMAIL_L1",
    EscalationLevel.L2_URGENT_PROVIDER_REVIEW: "ESCALATION_EMAIL_L2",
    EscalationLevel.L3_RAPID_RESPONSE_REVIEW: "ESCALATION_EMAIL_L3",
    EscalationLevel.L4_EMERGENCY_RESPONSE: "ESCALATION_EMAIL_L4",
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
        value = self._json_directory().get(normalized.value)
        if isinstance(value, str) and value.strip():
            return {"email": value.strip(), "role": normalized.value}
        if isinstance(value, dict):
            email = str(value.get("email") or "").strip()
            if email:
                return {
                    "email": email,
                    "role": str(value.get("role") or normalized.value).strip(),
                }

        env_key = _DEFAULT_ENV_KEYS.get(normalized)
        email = str(os.getenv(env_key or "", "") or "").strip() if env_key else ""
        if not email:
            return None
        return {"email": email, "role": normalized.value}


recipient_directory = RecipientDirectory()
