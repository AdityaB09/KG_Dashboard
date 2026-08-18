from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.escalation.levels import EscalationLevel, max_level, normalize_level


@dataclass(frozen=True)
class PolicyDecision:
    level: EscalationLevel
    policy_id: str
    policy_version: str
    default_level: EscalationLevel
    scenario_level: EscalationLevel
    evidence_level: EscalationLevel
    rules_fired: tuple[dict[str, Any], ...]
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyId": self.policy_id,
            "policyVersion": self.policy_version,
            "minimumLevel": self.level.value,
            "defaultLevel": self.default_level.value,
            "scenarioLevel": self.scenario_level.value,
            "evidenceLevel": self.evidence_level.value,
            "rulesFired": [dict(item) for item in self.rules_fired],
            "source": self.source,
        }


def _lookup(payload: dict[str, Any] | None, path: str) -> Any:
    value: Any = payload or {}
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _numeric(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _matches(rule: dict[str, Any], context: dict[str, Any]) -> tuple[bool, Any]:
    actual = _lookup(context, str(rule.get("path") or ""))
    operator = str(rule.get("operator") or "").strip().lower()
    if operator in {"gte", "lte", "gt", "lt"}:
        left = _numeric(actual)
        right = _numeric(rule.get("value"))
        if left is None or right is None:
            return False, actual
        return {
            "gte": left >= right,
            "lte": left <= right,
            "gt": left > right,
            "lt": left < right,
        }[operator], left
    if operator == "equalsany":
        values = {str(item).strip().lower() for item in rule.get("values", [])}
        return str(actual or "").strip().lower() in values, actual
    if operator == "containsany":
        text = str(actual or "").strip().lower()
        values = [str(item).strip().lower() for item in rule.get("values", [])]
        return any(item and item in text for item in values), actual
    return False, actual


class EscalationPolicyEngine:
    def _policy_path(self) -> Path:
        configured = str(getattr(settings, "ESCALATION_POLICY_PATH", "") or "").strip()
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path
            return path
        return Path(__file__).resolve().parent / "policies" / "cardinal_hospital_v1.json"

    def load_policy(self) -> tuple[dict[str, Any], str]:
        path = self._policy_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("policy root must be an object")
            return payload, str(path)
        except Exception:
            # Fail-safe configuration keeps the escalation engine operational even
            # if a policy artifact is accidentally missing/corrupt.
            fail_safe = normalize_level(
                getattr(settings, "ESCALATION_POLICY_FAIL_SAFE_LEVEL", "URGENT_PROVIDER_REVIEW"),
                default=EscalationLevel.URGENT_PROVIDER_REVIEW,
            )
            return {
                "policyId": "ORACLE-MILLENNIUM-HOSPITAL-RESPONSE-FAIL-SAFE",
                "version": "1",
                "defaultMinimumLevel": fail_safe.value,
                "failSafeLevel": fail_safe.value,
                "scenarioMinimums": {},
                "evidenceRules": [],
                "autoAdvanceSecondsByLevel": {},
            }, "fail_safe"

    def _env_scenario_map(self) -> dict[str, str]:
        raw = str(getattr(settings, "ESCALATION_SCENARIO_MINIMUMS_JSON", "{}") or "{}").strip() or "{}"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    def evaluate(
        self,
        *,
        scenario_id: str,
        policy_context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        policy, source = self.load_policy()
        default_level = normalize_level(
            policy.get("defaultMinimumLevel")
            or getattr(settings, "ESCALATION_POLICY_DEFAULT_LEVEL", "CARE_TEAM_REVIEW"),
            default=EscalationLevel.CARE_TEAM_REVIEW,
        )
        scenario_map = dict(policy.get("scenarioMinimums") or {})
        scenario_map.update(self._env_scenario_map())
        scenario_level = normalize_level(
            scenario_map.get(str(scenario_id)) or default_level.value,
            default=default_level,
        )

        evidence_level = EscalationLevel.MONITOR_ONLY
        fired: list[dict[str, Any]] = []
        context = policy_context if isinstance(policy_context, dict) else {}
        for rule in policy.get("evidenceRules") or []:
            if not isinstance(rule, dict):
                continue
            matched, actual = _matches(rule, context)
            if not matched:
                continue
            level = normalize_level(rule.get("minimumLevel"), default=EscalationLevel.MONITOR_ONLY)
            evidence_level = max_level(evidence_level, level)
            fired.append({
                "ruleId": str(rule.get("id") or "unnamed-rule"),
                "minimumLevel": level.value,
                "reason": str(rule.get("reason") or ""),
                "path": str(rule.get("path") or ""),
                "observed": actual,
            })

        minimum = max_level(default_level, scenario_level, evidence_level)
        return PolicyDecision(
            level=minimum,
            policy_id=str(policy.get("policyId") or getattr(settings, "ESCALATION_POLICY_ID", "ORACLE-MILLENNIUM-HOSPITAL-RESPONSE-V1")),
            policy_version=str(policy.get("version") or "1"),
            default_level=default_level,
            scenario_level=scenario_level,
            evidence_level=evidence_level,
            rules_fired=tuple(fired),
            source=source,
        )

    def minimum_level(
        self,
        *,
        scenario_id: str,
        model_response: dict[str, Any] | None = None,
        policy_context: dict[str, Any] | None = None,
    ) -> EscalationLevel:
        return self.evaluate(scenario_id=scenario_id, policy_context=policy_context).level

    def response_window_seconds(self, level: EscalationLevel | str) -> float:
        """Return the optional auto-advance window for a site response pathway.

        This is not an Oracle/Cerner ACK timer. It is a CARDINAL site policy used
        only when automatic escalation is explicitly enabled for the case.
        """
        normalized = normalize_level(level)
        override_raw = os.getenv("ESCALATION_AUTO_ADVANCE_SECONDS_JSON", "").strip()
        if override_raw:
            try:
                override = json.loads(override_raw)
            except json.JSONDecodeError:
                override = {}
            if isinstance(override, dict):
                for key, value in override.items():
                    try:
                        if normalize_level(key) == normalized:
                            return max(0.0, float(value))
                    except (TypeError, ValueError):
                        continue
        policy, _ = self.load_policy()
        configured = policy.get("autoAdvanceSecondsByLevel") or policy.get("ackTimeoutSecondsByLevel") or {}
        if isinstance(configured, dict):
            for key, value in configured.items():
                try:
                    if normalize_level(key) == normalized:
                        return max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
        return max(0.0, float(getattr(settings, "ESCALATION_RESPONSE_WINDOW_SECONDS", 120)))

    # Backward-compatible method name for older tests/integrations.
    def ack_timeout_seconds(self, level: EscalationLevel | str) -> float:
        return self.response_window_seconds(level)

    def public_summary(self) -> dict[str, Any]:
        policy, source = self.load_policy()
        return {
            "policyId": str(policy.get("policyId") or ""),
            "version": str(policy.get("version") or ""),
            "displayName": str(policy.get("displayName") or ""),
            "status": str(policy.get("status") or ""),
            "source": source,
            "defaultMinimumLevel": policy.get("defaultMinimumLevel"),
            "failSafeLevel": policy.get("failSafeLevel"),
            "autoAdvanceSecondsByLevel": policy.get("autoAdvanceSecondsByLevel") or policy.get("ackTimeoutSecondsByLevel") or {},
            "vendorSemantics": policy.get("vendorSemantics") or {},
            "scenarioMinimums": policy.get("scenarioMinimums") or {},
            "evidenceRuleCount": len(policy.get("evidenceRules") or []),
        }


policy_engine = EscalationPolicyEngine()
