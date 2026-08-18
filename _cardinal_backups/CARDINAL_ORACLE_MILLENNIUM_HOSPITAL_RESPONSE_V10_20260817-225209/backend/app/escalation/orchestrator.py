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
    is_terminal_level,
    level_label,
    level_role,
    next_level,
    normalize_level,
)
from app.escalation.models import build_case, new_correlation_id, new_event_id, now_iso, public_case
from app.escalation.notifications.email_service import email_service
from app.escalation.oracle.adapter import oracle_escalation_adapter
from app.escalation.policy_engine import policy_engine
from app.escalation.repository import escalation_repository


ACTIVE_CASE_STATUSES = {
    "CREATED",
    "ROUTING_STARTED",
    "ACK_PENDING",
    "ACK_TIMEOUT",
    "ACKNOWLEDGED",
    "RESPONSE_ACTIVE",
    "MONITORING",
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
        [
            provider or "cardinal",
            str(patient_id or "none"),
            str(encounter_id or "none"),
            str(episode_id),
            level.value,
        ]
    )


def _model_recommendation(model_response: dict[str, Any]) -> tuple[EscalationLevel, str, str]:
    recommendation = model_response.get("escalationRecommendation")
    if not isinstance(recommendation, dict):
        return (
            EscalationLevel.L0_MONITOR,
            "No escalation recommendation was returned by the model.",
            "low",
        )
    level = normalize_level(
        recommendation.get("levelCode"),
        default=EscalationLevel.L0_MONITOR,
    )
    rationale = str(recommendation.get("rationale") or "").strip()
    confidence = str(recommendation.get("confidence") or "").strip().lower() or "low"
    return level, rationale, confidence


def _provider_context(
    *,
    oracle_demo: dict[str, Any] | None,
    epic_demo: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    if isinstance(oracle_demo, dict) and oracle_demo:
        return "oracle", oracle_demo
    if isinstance(epic_demo, dict) and epic_demo:
        return "epic", epic_demo
    return "cardinal", {}


def _next_timeout_iso(level: EscalationLevel) -> str | None:
    seconds = policy_engine.ack_timeout_seconds(level)
    if seconds <= 0 or level in {
        EscalationLevel.L0_MONITOR,
        EscalationLevel.L4_EMERGENCY_RESPONSE,
    }:
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

        provider, vendor_context = _provider_context(
            oracle_demo=oracle_demo,
            epic_demo=epic_demo,
        )
        patient_id = str(
            vendor_context.get("patientId")
            or vendor_context.get("launchPatientId")
            or ""
        ).strip() or None
        encounter_id = str(vendor_context.get("encounterId") or "").strip() or None
        model_level, rationale, confidence = _model_recommendation(model_response)
        policy_decision = policy_engine.evaluate(
            scenario_id=scenario_id,
            policy_context=policy_context,
        )
        policy_level = policy_decision.level
        effective = adjudicate(
            model_suggested_level=model_level,
            policy_minimum_level=policy_level,
        )
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
                episode_patient.get("name")
                or episode_patient.get("display")
                or patient_id
                or ""
            ).strip()

        escalation_repository.create(case)
        append_audit_event(
            event_id,
            "MODEL_COMPLETE",
            detail="Etiology model returned an escalation recommendation.",
            data={
                "modelSuggestedLevel": model_level.value,
                "confidence": confidence,
            },
        )
        append_audit_event(
            event_id,
            "POLICY_EVALUATED",
            detail="CARDINAL policy floor was evaluated.",
            data={
                "policyId": policy_decision.policy_id,
                "policyVersion": policy_decision.policy_version,
                "policyMinimumLevel": policy_level.value,
                "scenarioLevel": policy_decision.scenario_level.value,
                "evidenceLevel": policy_decision.evidence_level.value,
                "rulesFired": [dict(item) for item in policy_decision.rules_fired],
                "effectiveLevel": effective.value,
            },
        )
        append_audit_event(
            event_id,
            "CASE_CREATED",
            detail=f"{effective.value} — {level_label(effective)}",
            data={"assignedRole": level_role(effective)},
            new_state="CREATED",
        )
        append_audit_event(
            event_id,
            "ESCALATION_CREATED",
            detail=f"{effective.value} — {level_label(effective)}",
        )

        if effective == EscalationLevel.L0_MONITOR:
            case = escalation_repository.update(
                event_id,
                lambda value: value.update({"status": "MONITORING", "nextEscalationAt": None, "ackDueAt": None}),
            )
            return public_case(case)

        case = await self._dispatch_level(event_id, effective, reason="initial_dispatch")
        return public_case(case)

    async def _dispatch_level(
        self,
        event_id: str,
        level: EscalationLevel,
        *,
        reason: str,
    ) -> dict[str, Any]:
        level = normalize_level(level)
        started_at = now_iso()

        def mark_started(case: dict[str, Any]) -> None:
            case["effectiveLevel"] = level.value
            case["effectiveLevelLabel"] = level_label(level)
            case["assignedRole"] = level_role(level)
            case["status"] = "ROUTING_STARTED"
            case["nextEscalationAt"] = None
            case["ackDueAt"] = None
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
            if not history or history[-1].get("level") != level.value:
                history.append(
                    {
                        "level": level.value,
                        "label": level_label(level),
                        "enteredAt": started_at,
                        "reason": reason,
                    }
                )

        case = escalation_repository.update(event_id, mark_started)
        append_audit_event(
            event_id,
            "ROUTING_STARTED",
            detail=f"Routing {level.value} to {level_role(level)}.",
            data={"level": level.value, "assignedRole": level_role(level), "reason": reason},
            previous_state=str(case.get("status") or "CREATED"),
            new_state="ROUTING_STARTED",
            reason=reason,
        )
        append_audit_event(
            event_id,
            "DELIVERY_ATTEMPTED",
            detail=f"Delivery channels started for {level.value}.",
            data={"level": level.value, "provider": case.get("provider")},
            delivery_result="ATTEMPTED",
        )

        provider = str(case.get("provider") or "cardinal").lower()
        if provider == "oracle":
            vendor_result = await oracle_escalation_adapter.dispatch(case)
        elif provider == "epic":
            vendor_result = await epic_escalation_adapter.dispatch(case)
        else:
            vendor_result = {"status": "skipped", "reason": "no_vendor_adapter"}

        # Refresh after vendor dispatch in case adapter used internal routing state.
        current = escalation_repository.get(event_id) or case
        email_result = await email_service.send(current)
        next_at = _next_timeout_iso(level)

        def finish(case_value: dict[str, Any]) -> None:
            delivery = {
                "level": level.value,
                "startedAt": started_at,
                "completedAt": now_iso(),
                "vendor": vendor_result,
                "email": email_result,
            }
            case_value["delivery"] = delivery
            case_value.setdefault("deliveryAttempts", []).append(delivery)
            case_value["status"] = "ACK_PENDING"
            case_value["nextEscalationAt"] = next_at
            case_value["ackDueAt"] = next_at

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
        delivery_ok = (
            email_result.get("status") == "sent"
            or vendor_result.get("status") in {"accepted", "active", "ready"}
        )
        append_audit_event(
            event_id,
            "DELIVERY_SUCCEEDED" if delivery_ok else "DELIVERY_FAILED",
            detail="At least one escalation channel accepted delivery." if delivery_ok else "No escalation channel reported successful delivery.",
            data={"vendor": vendor_result, "email": email_result},
            delivery_result="DELIVERED" if delivery_ok else "FAILED",
        )
        append_audit_event(
            event_id,
            "ACK_PENDING",
            detail="Escalation is awaiting acknowledgement.",
            data={"nextEscalationAt": next_at, "ackDueAt": next_at, "assignedRole": level_role(level)},
        )
        return escalation_repository.get(event_id) or case

    def acknowledge(
        self,
        event_id: str,
        *,
        acknowledged_by: str | None = None,
        acknowledged_role: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        if str(case.get("status") or "").upper() == "RESOLVED":
            return case
        acknowledged_at = now_iso()
        entered_at = None
        history = case.get("levelHistory") if isinstance(case.get("levelHistory"), list) else []
        if history:
            entered_at = history[-1].get("enteredAt")
        time_to_ack = None
        try:
            if entered_at:
                start_dt = datetime.fromisoformat(str(entered_at).replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(acknowledged_at.replace("Z", "+00:00"))
                time_to_ack = round(max(0.0, (end_dt - start_dt).total_seconds()), 3)
        except (TypeError, ValueError):
            time_to_ack = None

        def mutate(value: dict[str, Any]) -> None:
            value["status"] = "ACKNOWLEDGED"
            value["acknowledgedAt"] = acknowledged_at
            value["acknowledgedBy"] = str(acknowledged_by or settings.ESCALATION_DEFAULT_ACTOR or "CARDINAL recipient").strip()
            value["acknowledgedRole"] = str(acknowledged_role or settings.ESCALATION_DEFAULT_ACTOR_ROLE or "Clinical responder").strip()
            value["acknowledgementNote"] = str(note or "").strip() or None
            value["acknowledgedLevel"] = value.get("effectiveLevel")
            value["timeToAckSeconds"] = time_to_ack
            value["nextEscalationAt"] = None
            value["ackDueAt"] = None

        case = escalation_repository.update(event_id, mutate)
        append_audit_event(
            event_id,
            "ACKNOWLEDGED",
            detail="Escalation was acknowledged.",
            data={
                "acknowledgedBy": case.get("acknowledgedBy"),
                "acknowledgedLevel": case.get("acknowledgedLevel"),
                "acknowledgedRole": case.get("acknowledgedRole"),
                "timeToAckSeconds": case.get("timeToAckSeconds"),
                "note": case.get("acknowledgementNote"),
            },
            actor=case.get("acknowledgedBy"),
            actor_role=case.get("acknowledgedRole"),
            previous_state=str(case.get("status") or "ACK_PENDING"),
            new_state="ACKNOWLEDGED",
        )
        return escalation_repository.get(event_id) or case

    def resolve(
        self,
        event_id: str,
        *,
        resolved_by: str | None = None,
        resolved_role: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if not escalation_repository.get(event_id):
            raise KeyError(event_id)
        resolved_at = now_iso()

        def mutate(value: dict[str, Any]) -> None:
            value["status"] = "RESOLVED"
            value["resolvedAt"] = resolved_at
            value["resolvedBy"] = str(resolved_by or settings.ESCALATION_DEFAULT_ACTOR or "CARDINAL recipient").strip()
            value["resolvedRole"] = str(resolved_role or settings.ESCALATION_DEFAULT_ACTOR_ROLE or "Clinical responder").strip()
            value["resolutionNote"] = str(note or "").strip() or None
            value["nextEscalationAt"] = None
            value["ackDueAt"] = None

        case = escalation_repository.update(event_id, mutate)
        append_audit_event(
            event_id,
            "RESOLVED",
            detail="Escalation was resolved.",
            actor=case.get("resolvedBy"),
            actor_role=case.get("resolvedRole"),
            new_state="RESOLVED",
            data={"note": case.get("resolutionNote")},
        )
        return escalation_repository.get(event_id) or case

    async def escalate(self, event_id: str, *, reason: str = "manual_escalation") -> dict[str, Any]:
        case = escalation_repository.get(event_id)
        if not case:
            raise KeyError(event_id)
        current = normalize_level(case.get("effectiveLevel"))
        if is_terminal_level(current):
            return case
        target = next_level(current)
        append_audit_event(
            event_id,
            "ESCALATED",
            detail=f"Escalation advanced from {current.value} to {target.value}.",
            data={"from": current.value, "to": target.value, "reason": reason},
        )
        return await self._dispatch_level(event_id, target, reason=reason)

    async def process_due_timeouts(self) -> list[str]:
        processed: list[str] = []
        for case in escalation_repository.due_cases(now_iso()):
            event_id = str(case.get("eventId") or "")
            if not event_id:
                continue
            current = normalize_level(case.get("effectiveLevel"))
            if is_terminal_level(current):
                escalation_repository.update(
                    event_id,
                    lambda value: value.update({"nextEscalationAt": None, "ackDueAt": None}),
                )
                continue
            append_audit_event(
                event_id,
                "ACK_TIMEOUT",
                detail=f"Acknowledgement window expired at {current.value}.",
            )
            escalation_repository.update(
                event_id,
                lambda value: value.update({"status": "ACK_TIMEOUT", "nextEscalationAt": None, "ackDueAt": None}),
            )
            await self.escalate(event_id, reason="ack_timeout")
            processed.append(event_id)
        return processed


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
                "[CARDINAL ESCALATION TIMEOUT WORKER ERROR]",
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
