from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import Request

from app.config import settings
from app.evaluation_demo.mapping import resolve_patient_plan
from app.evaluation_injection.service import evaluation_injection_service
from app.oracle_smart import get_token_for_request


ACTIVE_STATES = {
    "ARMED",
    "INJECTING",
    "POST_EVENT",
    "ANALYZING",
}
TERMINAL_STATES = {
    "COMPLETE",
    "FAILED",
    "CANCELLED",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def _launch_identity(
    patient_id: str,
) -> dict[str, Any]:
    """
    Minimal SMART launch identity.

    This object is used only for routing and audit. It is not used by the SLM,
    validator, scorer, episode clinical panels, or patient-facing clinical
    context.
    """
    return {
        "id": patient_id,
        "fhirId": patient_id,
        "name": "Oracle SMART launch",
        "display": "Oracle SMART launch",
        "mrn": patient_id,
        "source": "oracle_smart_token",
        "clinicalContextUsed": False,
    }


def _episode_pack_summary(
    scenario_id: str,
) -> dict[str, Any]:
    configured = Path(
        settings.EVALUATION_INJECTION_DATASET_ROOT
    )

    if not configured.is_absolute():
        configured = (
            Path(__file__)
            .resolve()
            .parents[2]
            / configured
        ).resolve()

    path = (
        configured
        / "episodes"
        / f"{scenario_id}.json"
    )

    record = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    patient = dict(
        record.get("patient") or {}
    )
    patient.pop("disclaimer", None)

    return {
        "scenarioId": scenario_id,
        "schemaVersion": (
            record.get("schemaVersion")
            or "episode-slm-eval-v1"
        ),
        "patient": patient,
        "episode": dict(
            record.get("episode") or {}
        ),
        "vitals": dict(
            record.get("vitals") or {}
        ),
        "labs": dict(
            record.get("labs") or {}
        ),
        "medications": (
            list(record.get("medications") or [])
            if isinstance(
                record.get("medications"),
                list,
            )
            else []
        ),
        "clinicalContext": dict(
            record.get("clinicalContext") or {}
        ),
        "ecg": {
            "sampleRate": (
                (record.get("ecg") or {}).get("sampleRate")
            ),
            "durationSeconds": (
                (record.get("ecg") or {}).get("durationSeconds")
            ),
            "measurements": dict(
                (record.get("ecg") or {}).get("measurements")
                or {}
            ),
        },
        "clinicalContextSource": (
            "complete_episode_pack"
        ),
        "oracleFhirContextUsed": False,
    }


def _smart_session_key(
    token_state: dict[str, Any],
) -> str:
    existing = str(
        token_state.get("smart_session_id")
        or token_state.get("_oracle_demo_session_key")
        or ""
    ).strip()

    if existing:
        return existing

    generated = secrets.token_urlsafe(18)
    token_state["_oracle_demo_session_key"] = generated
    return generated


class OracleEvaluationDemoService:
    def __init__(self) -> None:
        self._lock = RLock()
        self._start_lock = asyncio.Lock()
        self._runs: dict[
            str,
            dict[str, Any],
        ] = {}

    def _run_for_smart_session(
        self,
        smart_session_key: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            runs = list(self._runs.values())

        for run in reversed(runs):
            if (
                str(run.get("smartSessionKey") or "")
                == smart_session_key
            ):
                return dict(run)

        return None

    def _run_for_waveform_session(
        self,
        waveform_session_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(
                waveform_session_id
            )
            return dict(run) if run else None

    @staticmethod
    def _public_demo(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            key: item
            for key, item in value.items()
            if key != "smartSessionId"
        }

    @classmethod
    def _public_run(
        cls,
        run: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not run:
            return None

        public = {
            key: value
            for key, value in run.items()
            if key != "smartSessionKey"
        }
        if "oracleDemo" in public:
            public["oracleDemo"] = cls._public_demo(public.get("oracleDemo"))
        return public

    @staticmethod
    def _status(
        waveform_session_id: str,
    ) -> dict[str, Any]:
        try:
            value = evaluation_injection_service.status(
                waveform_session_id
            )
            return (
                dict(value)
                if isinstance(value, dict)
                else {"state": "IDLE"}
            )
        except Exception:
            return {"state": "IDLE"}

    async def bootstrap(
        self,
        request: Request,
    ) -> dict[str, Any]:
        if not settings.ORACLE_EVALUATION_DEMO_ENABLED:
            return {
                "ready": False,
                "enabled": False,
                "reason": (
                    "oracle_evaluation_demo_disabled"
                ),
            }

        if not evaluation_injection_service.enabled:
            return {
                "ready": False,
                "enabled": True,
                "reason": (
                    "evaluation_injection_disabled"
                ),
            }

        token_state = get_token_for_request(
            request
        )
        if not token_state:
            return {
                "ready": False,
                "enabled": True,
                "reason": (
                    "oracle_smart_session_missing"
                ),
            }

        patient_id = str(
            token_state.get("patient_id")
            or ""
        ).strip()

        if not patient_id:
            return {
                "ready": False,
                "enabled": True,
                "reason": (
                    "oracle_patient_context_missing"
                ),
            }

        smart_session_key = _smart_session_key(
            token_state
        )

        # Returning to the waveform page or refreshing the browser must reuse
        # the run created by this same Oracle SMART authorization.
        existing = self._run_for_smart_session(
            smart_session_key
        )
        if existing:
            waveform_session_id = str(
                existing.get(
                    "waveformSessionId"
                )
                or ""
            )
            status = self._status(
                waveform_session_id
            )
            oracle_demo = (
                existing.get("oracleDemo")
                or {}
            )
            plan = resolve_patient_plan(
                patient_id=patient_id,
                patient_display=None,
                selection_key=smart_session_key,
            )
            episode_pack = _episode_pack_summary(
                plan["scenarioId"]
            )

            return {
                "ready": True,
                "enabled": True,
                "mode": (
                    "oracle_evaluation_auto"
                ),
                "patient": _launch_identity(patient_id),
                "episodePack": episode_pack,
                "clinicalContextMode": "episode_pack_only",
                "oracleFhirContextUsed": False,
                "patientResourceLoaded": False,
                "patientResourceFromCache": False,
                "patientResourceError": None,
                "encounterId": token_state.get(
                    "encounter_id"
                ),
                "scenario": plan,
                "existingRun": {
                    **status,
                    "reused": True,
                    "waveformSessionId": (
                        waveform_session_id
                    ),
                    "demoRunId": existing.get(
                        "demoRunId"
                    ),
                },
            }

        launch_identity = _launch_identity(
            patient_id
        )

        plan = resolve_patient_plan(
            patient_id=patient_id,
            patient_display=None,
            selection_key=smart_session_key,
        )

        episode_pack = _episode_pack_summary(
            plan["scenarioId"]
        )

        return {
            "ready": True,
            "enabled": True,
            "mode": (
                "oracle_evaluation_auto"
            ),
            "clinicalContextMode": (
                "episode_pack_only"
            ),
            "patient": launch_identity,
            "episodePack": episode_pack,
            "patientResourceLoaded": False,
            "patientResourceFromCache": False,
            "patientResourceError": None,
            "oracleFhirContextUsed": False,
            "encounterId": token_state.get(
                "encounter_id"
            ),
            "scenario": plan,
            "existingRun": None,
        }

    async def start(
        self,
        request: Request,
        *,
        waveform_session_id: str,
    ) -> dict[str, Any]:
        async with self._start_lock:
            token_state = get_token_for_request(
                request
            )
            if not token_state:
                raise RuntimeError(
                    "Oracle SMART session is unavailable."
                )

            smart_session_key = _smart_session_key(
                token_state
            )

            # Strong idempotency across React remounts, browser refreshes, and
            # repeated POST requests for the same Oracle authorization.
            existing = self._run_for_smart_session(
                smart_session_key
            )
            if existing:
                existing_waveform_id = str(
                    existing.get(
                        "waveformSessionId"
                    )
                    or waveform_session_id
                )
                current = self._status(
                    existing_waveform_id
                )
                return {
                    **current,
                    "reused": True,
                    "mode": (
                        "oracle_evaluation_auto"
                    ),
                    "oracleDemo": self._public_demo(
                        existing.get("oracleDemo")
                        or current.get(
                            "oracleDemo"
                        )
                    ),
                }

            bootstrap = await self.bootstrap(
                request
            )
            if not bootstrap.get("ready"):
                raise RuntimeError(
                    str(
                        bootstrap.get("message")
                        or bootstrap.get("reason")
                        or (
                            "Oracle evaluation demo "
                            "is not ready."
                        )
                    )
                )

            current = self._status(
                waveform_session_id
            )
            current_state = str(
                current.get("state")
                or "IDLE"
            ).upper()

            current_run = (
                self._run_for_waveform_session(
                    waveform_session_id
                )
            )

            # Never overlap two evaluations on one waveform stream.
            if current_state in ACTIVE_STATES:
                return {
                    **current,
                    "reused": True,
                    "mode": (
                        "oracle_evaluation_auto"
                    ),
                }

            # COMPLETE from the same SMART authorization is terminal and reused.
            if (
                current_state in TERMINAL_STATES
                and current_run
                and str(
                    current_run.get(
                        "smartSessionKey"
                    )
                    or ""
                )
                == smart_session_key
            ):
                return {
                    **current,
                    "reused": True,
                    "mode": (
                        "oracle_evaluation_auto"
                    ),
                }

            plan = bootstrap["scenario"]
            patient = bootstrap["patient"]
            demo_run_id = (
                "oracle-eval-"
                + secrets.token_urlsafe(12)
            )

            oracle_demo = {
                "mode": (
                    "oracle_evaluation_auto"
                ),
                "demoRunId": demo_run_id,
                "smartSessionId": str(token_state.get("smart_session_id") or ""),
                "patientId": patient["id"],
                "encounterId": (
                    bootstrap.get(
                        "encounterId"
                    )
                ),
                "launchPatientId": patient["id"],
                "launchIdentity": _launch_identity(
                    patient["id"]
                ),
                "episodePackPatient": (
                    bootstrap["episodePack"]
                    .get("patient")
                ),
                "clinicalContextMode": (
                    "episode_pack_only"
                ),
                "clinicalContextSource": (
                    "complete_episode_pack"
                ),
                "oracleFhirContextUsed": False,
                "scenarioId": (
                    plan["scenarioId"]
                ),
                "mappingSource": (
                    plan.get(
                        "mappingSource"
                    )
                ),
                "mappingNote": (
                    plan.get(
                        "mappingNote"
                    )
                ),
                "startedAt": _now_iso(),
            }

            armed = (
                evaluation_injection_service.arm(
                    session_id=(
                        waveform_session_id
                    ),
                    scenario_id=(
                        plan["scenarioId"]
                    ),
                    baseline_seconds=(
                        plan["baselineSeconds"]
                    ),
                    pre_seconds=(
                        plan["preSeconds"]
                    ),
                    post_seconds=(
                        plan["postSeconds"]
                    ),
                    run_slm=plan["runSlm"],
                    oracle_demo=oracle_demo,
                    # Oracle SMART has completed routing. No token is
                    # passed into the clinical evaluation pipeline.
                    token_override=None,
                )
            )

            with self._lock:
                self._runs[
                    waveform_session_id
                ] = {
                    "demoRunId": demo_run_id,
                    "waveformSessionId": (
                        waveform_session_id
                    ),
                    "smartSessionKey": (
                        smart_session_key
                    ),
                    "oracleDemo": (
                        oracle_demo
                    ),
                    "createdAt": _now_iso(),
                }

            return {
                **armed,
                "mode": (
                    "oracle_evaluation_auto"
                ),
                "oracleDemo": self._public_demo(oracle_demo),
                "episodePack": bootstrap["episodePack"],
                "clinicalContextMode": "episode_pack_only",
                "oracleFhirContextUsed": False,
                "reused": False,
            }

    def status(
        self,
        waveform_session_id: str,
    ) -> dict[str, Any]:
        status = self._status(
            waveform_session_id
        )
        run = self._run_for_waveform_session(
            waveform_session_id
        )

        return {
            **status,
            "mode": (
                "oracle_evaluation_auto"
            ),
            "demoRun": self._public_run(
                run
            ),
        }

    def cancel(
        self,
        waveform_session_id: str,
    ) -> dict[str, Any]:
        return evaluation_injection_service.cancel(
            waveform_session_id
        )


oracle_evaluation_demo_service = (
    OracleEvaluationDemoService()
)
