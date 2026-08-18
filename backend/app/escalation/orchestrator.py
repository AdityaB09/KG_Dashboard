from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.escalation.adjudicator import adjudicate
from app.escalation.audit import append_audit_event
from app.escalation.epic.adapter import epic_escalation_adapter
from app.escalation.levels import (
    EscalationLevel,
    is_auto_advance_terminal,
    is_terminal_level,
    level_label,
    level_role,
    next_level,
    normalize_level,
    tier_code,
)
from app.escalation.models import build_case, new_correlation_id, new_event_id, now_iso, public_case
from app.escalation.notifications.email_service import email_service
from app.escalation.notifications.teams_service import teams_workflow_service
from app.escalation.oracle.adapter import oracle_escalation_adapter
from app.escalation.policy_engine import policy_engine
from app.escalation.repository import escalation_repository


ACTIVE_CASE_STATUSES = {
    "CREATED",
    "ROUTING_STARTED",
    "ROUTED",
    "ROUTED_AUTO_ADVANCE",
    "ROUTED_TERMINAL",
    "MONITORING",
    # Historical V6-V9 states remain readable.
    "ACK_PENDING",
    "ACK_TIMEOUT",
    "ACKNOWLEDGED",
    "RESPONSE_ACTIVE",
}


def _idempotency_key(
    *,
    provider: str,
    patient_id: str | None,
    encounter_id: str | None,
    episode_id: str,
    level: EscalationLevel,
) -> str:
    return ":".join(
        [provider or "cardinal", str(patient_id or "none"), str(encounter_id or "none"), str(episode_id), level.value]
    )


def _model_recommendation(model_response: dict[str, Any]) -> tuple[EscalationLevel, str, str]:
    recommendation = model_response.get("escalationRecommendation")
    if not isinstance(recommendation, dict):
        return EscalationLevel.MONITOR_ONLY, "No response-pathway recommendation was returned by the model.", "low"
    level = normalize_level(recommendation.get("levelCode"), default=EscalationLevel.MONITOR_ONLY)
    rationale = str(recommendation.get("rationale") or "").strip()
    confidence = str(recommendation.get("confidence") or "").strip().lower() or "low"
    return level, rationale, confidence


def _provider_context(
    *, oracle_demo: dict[str, Any] | None, epic_demo: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    if isinstance(oracle_demo, dict) and oracle_demo:
        return "oracle", oracle_demo
    if isinstance(epic_demo, dict) and epic_demo:
        return "epic", epic_demo
    return "cardinal", {}


def _next_response_window_iso(level: EscalationLevel, enabled: bool) -> str | None:
    if not enabled or is_auto_advance_terminal(level) or level == EscalationLevel.MONITOR_ONLY:
        return None
    seconds = policy_engine.response_window_seconds(level)
    if seconds <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class EscalationOrchestrator:
    @property
    def enabled(self) -> bool:
        return bool(settings.ESCALATION_ENABLED)

    async def evaluate_and_dispatch(
        self,
        *,
        episode_id: str,
        incident_id: str,
        scenario_id: str,
        model_response: dict[str, Any] | None,
        oracle_demo: dict[str, Any] | None,
        epic_demo: dict[str, Any] | None,
        waveform_session_id: str,
        policy_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "enabled": False}
        if not isinstance(model_response, dict):
            return {"status": "skipped", "enabled": True, "reason": "model_response_unavailable"}

        provider, vendor_context = _provider_context(oracle_demo=oracle_demo, epic_demo=epic_demo)
        patient_id = str(vendor_context.get("patientId") or vendor_context.get("launchPatientId") or "").strip() or None
        encounter_id = str(vendor_context.get("encounterId") or "").strip() or None
        model_level, rationale, confidence = _model_recommendation(model_response)
        policy_decision = policy_engine.evaluate(scenario_id=scenario_id, policy_context=policy_context)
        policy_level = policy_decision.level
        effective = adjudicate(model_suggested_level=model_level, policy_minimum_level=policy_level)
        idempotency = _idempotency_key(
            provider=provider,
            patient_id=patient_id,
            encounter_id=encounter_id,
            episode_id=episode_id,
            level=effective,
        )
        existing = escalation_repository.find_by_idempotency_key(idempotency)
        if existing:
            return {**public_case(existing), "reused": True}

        event_id = new_event_id()
        routing_context = {
            "smartSessionId": str(vendor_context.get("smartSessionId") or ""),
            "provider": provider,
            "patientId": patient_id,
            "encounterId": encounter_id,
            "scenarioId": scenario_id,
        }
        case = build_case(
            event_id=event_id,
            episode_id=episode_id,
            incident_id=incident_id,
            scenario_id=scenario_id,
            provider=provider,
            patient_id=patient_id,
            encounter_id=encounter_id,
            model_suggested_level=model_level,
            policy_minimum_level=policy_level,
            effective_level=effective,
            model_rationale=rationale,
            model_confidence=confidence,
            model_response=model_response,
            waveform_session_id=waveform_session_id,
            routing_context=routing_context,
            idempotency_key=idempotency,
        )
        case["correlationId"] = new_correlation_id()
        case["policyDecision"] = policy_decision.as_dict()
        case["policyId"] = policy_decision.policy_id
        case["policyVersion"] = policy_decision.policy_version
        case["siteId"] = settings.ESCALATION_SITE_ID
        case["facilityId"] = settings.ESCALATION_FACILITY_ID
        case["serviceId"] = settings.ESCALATION_SERVICE_ID
        case["instanceId"] = settings.ESCALATION_INSTANCE_ID
        case["policyContextSnapshot"] = dict(policy_context or {})
        episode_patient = vendor_context.get("episodePackPatient")
        if isinstance(episode_patient, dict):
            case["patientDisplayName"] = str(
                episode_patient.get("name") or episode_patient.get("display") or patient_id or ""
            ).strip()

        escalation_repository.create(case)
        append_audit_event(
            event_id,
            "MODEL_COMPLETE",
            detail="Etiology model returned a clinical response-pathway recommendation.",
            data={"modelSuggestedLevel": model_level.value, "responseTier": tier_code(model_level), "confidence": confidence},
        )
        append_audit_event(
            event_id,
            "POLICY_EVALUATED",
            detail="Site hospital response policy was evaluated.",
            data={
                "policyId": policy_decision.policy_id,
                "policyVersion": policy_decision.policy_version,
                "policyMinimumLevel": policy_level.value,
                "scenarioLevel": policy_decision.scenario_level.value,
                "evidenceLevel": policy_decision.evidence_level.value,
                "rulesFired": [dict(item) for item in policy_decision.rules_fired],
                "effectiveLevel": effective.value,
                "responseTier": tier_code(effective),
            },
        )
        append_audit_event(
            event_id,
            "CASE_CREATED",
            detail=f"{level_label(effective)} — {level_role(effective)}",
            data={"responsePathway": effective.value, "responseTier": tier_code(effective), "assignedRole": level_role(effective)},
            new_state="CREATED",
        )
        append_audit_event(
            event_id,
            "RESPONSE_PATHWAY_SELECTED",
            detail=f"Selected hospital response pathway: {level_label(effective)}.",
            data={"responsePathway": effective.value, "responseTier": tier_code(effective)},
        )

        if effective == EscalationLevel.MONITOR_ONLY:
            case = escalation_repository.update(
                event_id,
                lambda value: value.update({"status": "MONITORING", "nextEscalationAt": None}),
            )
            return public_case(case)

        case = await self._dispatch_level(event_id, effective, reason="initial_dispatch")
        return public_case(case)

    async def _dispatch_level(self, event_id: str, level: EscalationLevel, *, reason: str) -> dict[str, Any]:
        level = normalize_level(level)
        started_at = now_iso()

        def mark_started(case: dict[str, Any]) -> None:
            case["effectiveLevel"] = level.value
            case["effectiveLevelLabel"] = level_label(level)
            case["assignedRole"] = level_role(level)
            case["responseTierCode"] = tier_code(level)
            case["status"] = "ROUTING_STARTED"
            case["nextEscalationAt"] = None
            # Remove historical ACK deadline from new routing stages.
            case.pop("ackDueAt", None)
            level_idempotency_key = _idempotency_key(
                provider=str(case.get("provider") or "cardinal"),
                patient_id=case.get("patientId"),
                encounter_id=case.get("encounterId"),
                episode_id=str(case.get("episodeId") or ""),
                level=level,
            )
            keys = case.setdefault("idempotencyKeys", [])
            if level_idempotency_key not in keys:
                keys.append(level_idempotency_key)
            history = case.setdefault("levelHistory", [])
            if not history or normalize_level(history[-1].get("level"), default=EscalationLevel.MONITOR_ONLY) != level:
                history.append({"level": level.value, "label": level_label(level), "tierCode": tier_code(level), "enteredAt": started_at, "reason": reason})

        case = escalation_repository.update(event_id, mark_started)
        append_audit_event(
            event_id,
            "ROUTING_STARTED",
            detail=f"Routing {level_label(level)} to {level_role(level)}.",
            data={"responsePathway": level.value, "responseTier": tier_code(level), "assignedRole": level_role(level), "reason": reason},
            new_state="ROUTING_STARTED",
            reason=reason,
        )
        append_audit_event(
            event_id,
            "DELIVERY_ATTEMPTED",
            detail=f"Delivery channels started for {level_label(level)}.",
            data={"responsePathway": level.value, "responseTier": tier_code(level), "provider": case.get("provider")},
            delivery_result="ATTEMPTED",
        )

        provider = str(case.get("provider") or "cardinal").lower()
        if provider == "oracle":
            vendor_result = await oracle_escalation_adapter.dispatch(case)
        elif provider == "epic":
            vendor_result = await epic_escalation_adapter.dispatch(case)
        else:
            vendor_result = {"status": "skipped", "reason": "no_vendor_adapter"}

        current = escalation_repository.get(event_id) or case
        email_result, teams_result = await asyncio.gather(
            email_service.send(current),
            teams_workflow_service.send(current),
        )
        auto_enabled = bool(current.get("autoEscalationEnabled", settings.ESCALATION_AUTO_ADVANCE_DEFAULT)) and not is_auto_advance_terminal(level)
        next_at = _next_response_window_iso(level, auto_enabled)

        def finish(case_value: dict[str, Any]) -> None:
            delivery = {
                "level": level.value,
                "responsePathway": level.value,
                "responseTier": tier_code(level),
                "startedAt": started_at,
                "completedAt": now_iso(),
                "vendor": vendor_result,
                "email": email_result,
                "teams": teams_result,
            }
            case_value["delivery"] = delivery
            case_value.setdefault("deliveryAttempts", []).append(delivery)
            case_value["autoEscalationEnabled"] = auto_enabled
            case_value["nextEscalationAt"] = next_at
            if is_terminal_level(level):
                case_value["status"] = "ROUTED_TERMINAL"
            elif auto_enabled and next_at:
                case_value["status"] = "ROUTED_AUTO_ADVANCE"
            else:
                case_value["status"] = "ROUTED"

        case = escalation_repository.update(event_id, finish)
        append_audit_event(
            event_id,
            "VENDOR_ACCEPTED" if vendor_result.get("status") in {"accepted", "active"} else "VENDOR_ROUTING_RECORDED",
            detail=f"{provider} routing status: {vendor_result.get('status')}",
            data={"vendor": vendor_result},
        )
        append_audit_event(
            event_id,
            "EMAIL_SENT" if email_result.get("status") == "sent" else "EMAIL_STATUS_RECORDED",
            detail=f"Email status: {email_result.get('status')}",
            data={"email": email_result},
        )
        append_audit_event(
            event_id,
            "TEAMS_SENT" if teams_result.get("status") == "sent" else "TEAMS_STATUS_RECORDED",
            detail=f"Microsoft Teams workflow status: {teams_result.get('status')}",
            data={"teams": teams_result},
            http_status=teams_result.get("httpStatus"),
        )
        delivery_ok = (
            email_result.get("status") == "sent"
            or teams_result.get("status") == "sent"
            or vendor_result.get("status") in {"accepted", "active", "ready"}
        )
        append_audit_event(
            event_id,
            "DELIVERY_SUCCEEDED" if delivery_ok else "DELIVERY_FAILED",
            detail="At least one clinical response channel accepted delivery." if delivery_ok else "No clinical response channel reported successful delivery.",
            data={"vendor": vendor_result, "email": email_result, "teams": teams_result},
            delivery_result="DELIVERED" if delivery_ok else "FAILED",
        )
        append_audit_event(
            event_id,
            "AUTO_ESCALATION_SCHEDULED" if next_at else "AUTO_ESCALATION_NOT_SCHEDULED",
            detail=(
                f"Automatic escalation is ON; next pathway review is due at {next_at}."
                if next_at
                else ("Automatic progression stops at Rapid Response / Emergency Override;" if is_auto_advance_terminal(level) else "")
            ),
            data={"enabled": auto_enabled, "nextEscalationAt": next_at, "responsePathway": level.value, "responseTier": tier_code(level)},
        )
        return escalation_repository.get(event_id) or case

    def set_auto_escalation(self, event_id: str, *, enabled: bool) -> dict[str, Any]:
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        current = normalize_level(case.get("effectiveLevel"), default=EscalationLevel.MONITOR_ONLY)
        effective_enabled = bool(enabled) and not is_auto_advance_terminal(current) and current != EscalationLevel.MONITOR_ONLY
        next_at = _next_response_window_iso(current, effective_enabled)

        def mutate(value: dict[str, Any]) -> None:
            value["autoEscalationEnabled"] = effective_enabled
            value["nextEscalationAt"] = next_at
            if is_auto_advance_terminal(current):
                value["status"] = "ROUTED_TERMINAL"
            elif effective_enabled and next_at:
                value["status"] = "ROUTED_AUTO_ADVANCE"
            elif str(value.get("status") or "") != "MONITORING":
                value["status"] = "ROUTED"
            value.pop("ackDueAt", None)

        updated = escalation_repository.update(event_id, mutate)
        append_audit_event(
            event_id,
            "AUTO_ESCALATION_ENABLED" if effective_enabled else "AUTO_ESCALATION_DISABLED",
            detail=(
                "Automatic pathway escalation enabled for this case."
                if effective_enabled
                else "Automatic pathway escalation disabled for this case."
            ),
            data={"enabled": effective_enabled, "nextEscalationAt": next_at, "responsePathway": current.value, "responseTier": tier_code(current)},
        )
        return public_case(escalation_repository.get(event_id) or updated)

    async def escalate(self, event_id: str, *, reason: str = "automatic_response_window") -> dict[str, Any]:
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        current = normalize_level(case.get("effectiveLevel"))
        if is_auto_advance_terminal(current):
            return case
        target = next_level(current)
        append_audit_event(
            event_id,
            "RESPONSE_PATHWAY_ADVANCED",
            detail=f"Clinical response advanced from {level_label(current)} to {level_label(target)}.",
            data={"from": current.value, "fromTier": tier_code(current), "to": target.value, "toTier": tier_code(target), "reason": reason},
        )
        return await self._dispatch_level(event_id, target, reason=reason)

    async def process_due_timeouts(self) -> list[str]:
        """Advance only cases whose per-case automatic escalation toggle is ON."""
        processed: list[str] = []
        for case in escalation_repository.due_cases(now_iso()):
            event_id = str(case.get("eventId") or "")
            if not event_id:
                continue
            current = normalize_level(case.get("effectiveLevel"))
            if is_terminal_level(current):
                escalation_repository.update(event_id, lambda value: value.update({"nextEscalationAt": None}))
                continue
            append_audit_event(
                event_id,
                "AUTO_ESCALATION_WINDOW_EXPIRED",
                detail=f"Configured response window expired for {level_label(current)}.",
                data={"responsePathway": current.value, "responseTier": tier_code(current)},
            )
            escalation_repository.update(event_id, lambda value: value.update({"nextEscalationAt": None}))
            await self.escalate(event_id, reason="automatic_response_window")
            processed.append(event_id)
        return processed

    # Historical API methods are intentionally disabled by default in V10. They
    # can be re-enabled only to support an older external client during migration.
    def acknowledge(self, event_id: str, **_: Any) -> dict[str, Any]:
        if not settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED:
            raise RuntimeError("Manual acknowledgement is disabled by the V10 hospital-response workflow.")
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        return public_case(case)

    def resolve(self, event_id: str, **_: Any) -> dict[str, Any]:
        if not settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED:
            raise RuntimeError("Manual resolution is disabled by the V10 hospital-response workflow.")
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        return public_case(case)


escalation_orchestrator = EscalationOrchestrator()
_timeout_worker_task: asyncio.Task | None = None


async def _timeout_worker() -> None:
    while True:
        try:
            if escalation_orchestrator.enabled:
                await escalation_orchestrator.process_due_timeouts()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                "[CARDINAL RESPONSE WORKER ERROR]",
                {"errorType": type(exc).__name__, "message": str(exc)},
                flush=True,
            )
        await asyncio.sleep(max(1.0, float(settings.ESCALATION_TIMEOUT_POLL_SECONDS)))


def start_timeout_worker() -> None:
    global _timeout_worker_task
    if not escalation_orchestrator.enabled:
        return
    if _timeout_worker_task is None or _timeout_worker_task.done():
        _timeout_worker_task = asyncio.create_task(_timeout_worker())


async def stop_timeout_worker() -> None:
    global _timeout_worker_task
    task = _timeout_worker_task
    _timeout_worker_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
