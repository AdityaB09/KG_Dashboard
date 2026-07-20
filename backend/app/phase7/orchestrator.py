from __future__ import annotations

import asyncio
import traceback
from dataclasses import asdict
from typing import Any, Mapping

from app.phase7.adapters import (
    analyze_episode_sync,
    analyze_incident_sync,
    build_slm_context_sync,
    get_episode,
    get_incident,
    get_incident_episodes,
    publish_event,
)
from app.phase7.config import (
    phase7_settings,
)
from app.phase7.evidence import (
    build_evidence_package,
)
from app.phase7.io import (
    evidence_path,
    now_iso,
    prompt_path,
    read_json,
    slm_response_path,
    status_path,
    write_json_atomic,
)
from app.phase7.oracle_context import (
    load_or_reuse_clinical_context,
)
from app.phase7.prompt_builder import (
    build_prompt_package,
)
from app.phase7.slm_client import (
    run_slm,
)


class Phase7Orchestrator:
    def __init__(self) -> None:
        self._incident_locks: dict[
            str,
            asyncio.Lock,
        ] = {}

        self._revisions: dict[
            str,
            int,
        ] = {}

        self._tasks: set[
            asyncio.Task[Any]
        ] = set()

    def _lock(
        self,
        incident_id: str,
    ) -> asyncio.Lock:
        lock = self._incident_locks.get(
            incident_id
        )

        if lock is None:
            lock = asyncio.Lock()
            self._incident_locks[
                incident_id
            ] = lock

        return lock

    def _write_status(
        self,
        incident_id: str,
        *,
        state: str,
        stage: str,
        detail: str | None = None,
        error: Mapping[
            str,
            Any,
        ]
        | None = None,
        outputs: Mapping[
            str,
            Any,
        ]
        | None = None,
    ) -> dict[str, Any]:
        previous: dict[
            str,
            Any,
        ] = {}

        try:
            previous = read_json(
                status_path(
                    incident_id
                )
            )
        except FileNotFoundError:
            pass

        value = {
            "schemaVersion": (
                phase7_settings
                .schema_version
            ),
            "incidentId": incident_id,
            "state": state,
            "stage": stage,
            "detail": detail,
            "createdAt": (
                previous.get(
                    "createdAt"
                )
                or now_iso()
            ),
            "updatedAt": now_iso(),
            "automatic": True,
            "error": (
                dict(error)
                if error
                else None
            ),
            "outputs": (
                dict(outputs)
                if outputs
                else previous.get(
                    "outputs"
                )
                or {}
            ),
        }

        write_json_atomic(
            status_path(
                incident_id
            ),
            value,
        )

        return value

    def schedule_captured_episode(
        self,
        *,
        episode_id: str,
        incident_id: str | None,
    ) -> bool:
        if not (
            phase7_settings.enabled
            and phase7_settings
            .auto_run_after_capture
        ):
            return False

        try:
            loop = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            return False

        key = (
            incident_id
            or f"episode:{episode_id}"
        )

        revision = (
            self._revisions.get(
                key,
                0,
            )
            + 1
        )

        self._revisions[
            key
        ] = revision

        task = loop.create_task(
            self._delayed_episode_run(
                episode_id=episode_id,
                incident_id=incident_id,
                key=key,
                revision=revision,
            )
        )

        self._tasks.add(task)

        task.add_done_callback(
            self._tasks.discard
        )

        return True

    async def _delayed_episode_run(
        self,
        *,
        episode_id: str,
        incident_id: str | None,
        key: str,
        revision: int,
    ) -> None:
        await asyncio.sleep(
            phase7_settings
            .debounce_seconds
        )

        if (
            self._revisions.get(key)
            != revision
        ):
            return

        if not incident_id:
            episode = get_episode(
                episode_id
            )

            incident_id = episode.get(
                "incidentId"
            )

        if not incident_id:
            return

        await self.run_incident(
            incident_id=incident_id,
            force=False,
            force_context=False,
            run_model=(
                phase7_settings
                .run_slm_automatically
            ),
            trigger_episode_id=(
                episode_id
            ),
        )

    async def run_incident(
        self,
        *,
        incident_id: str,
        force: bool,
        force_context: bool,
        run_model: bool,
        trigger_episode_id: str | None = None,
        token_override: Mapping[
            str,
            Any,
        ]
        | None = None,
        requested_patient_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock(
            incident_id
        ):
            self._write_status(
                incident_id,
                state="running",
                stage=(
                    "episode_analysis"
                ),
                detail=(
                    "Automatic Phase 7 "
                    "processing started."
                ),
            )

            publish_event(
                {
                    "type": (
                        "phase7.started"
                    ),
                    "incidentId": (
                        incident_id
                    ),
                    "episodeId": (
                        trigger_episode_id
                    ),
                }
            )

            try:
                incident = get_incident(
                    incident_id
                )

                episodes = (
                    get_incident_episodes(
                        incident_id
                    )
                )

                episode_results: list[
                    dict[str, Any]
                ] = []

                for episode in episodes:
                    episode_id = (
                        episode.get("id")
                    )

                    if not episode_id:
                        continue

                    result = await (
                        asyncio.to_thread(
                            analyze_episode_sync,
                            episode_id,
                            force=force,
                        )
                    )

                    episode_results.append(
                        {
                            "episodeId": (
                                episode_id
                            ),
                            "status": (
                                result.get(
                                    "status"
                                )
                            ),
                            "algorithmVersion": (
                                result.get(
                                    "algorithmVersion"
                                )
                            ),
                        }
                    )

                self._write_status(
                    incident_id,
                    state="running",
                    stage=(
                        "incident_analysis"
                    ),
                    detail=(
                        f"Analyzed "
                        f"{len(episode_results)} "
                        "episode view(s)."
                    ),
                )

                incident_analysis = await (
                    asyncio.to_thread(
                        analyze_incident_sync,
                        incident_id,
                        force=force,
                    )
                )

                self._write_status(
                    incident_id,
                    state="running",
                    stage=(
                        "clinical_context"
                    ),
                    detail=(
                        "Loading or reusing "
                        "incident-linked clinical "
                        "context."
                    ),
                )

                if (
                    phase7_settings
                    .load_clinical_context
                ):
                    (
                        clinical_context,
                        credentials,
                    ) = await (
                        load_or_reuse_clinical_context(
                            incident_id=(
                                incident_id
                            ),
                            force=(
                                force_context
                            ),
                            token_override=(
                                token_override
                            ),
                            requested_patient_id=(
                                requested_patient_id
                            ),
                        )
                    )

                else:
                    from app.clinical_context import (
                        clinical_context_service,
                    )

                    clinical_context = dict(
                        clinical_context_service
                        .get(incident_id)
                    )

                    credentials = None

                context_resolution = (
                    asdict(credentials)
                    if credentials
                    is not None
                    else {
                        "source": (
                            "context_loading_disabled"
                        ),
                        "patient_id": None,
                        "fhir_base_url": None,
                        "session_count": 0,
                        "limitation": (
                            "Phase 7 automatic "
                            "clinical-context loading "
                            "is disabled."
                        ),
                    }
                )

                # Never persist an access token in
                # evidence or status output.
                context_resolution.pop(
                    "access_token",
                    None,
                )

                self._write_status(
                    incident_id,
                    state="running",
                    stage="slm_context",
                    detail=(
                        "Building the compact "
                        "Phase 6 SLM context."
                    ),
                )

                slm_context = await (
                    asyncio.to_thread(
                        build_slm_context_sync,
                        incident_id,
                    )
                )

                current_incident = (
                    get_incident(
                        incident_id
                    )
                )

                evidence = (
                    build_evidence_package(
                        incident=(
                            current_incident
                        ),
                        incident_analysis=(
                            incident_analysis
                        ),
                        slm_context=(
                            slm_context
                        ),
                        clinical_context=(
                            clinical_context
                        ),
                        context_resolution=(
                            context_resolution
                        ),
                        schema_version=(
                            phase7_settings
                            .schema_version
                        ),
                    )
                )

                write_json_atomic(
                    evidence_path(
                        incident_id
                    ),
                    evidence,
                )

                self._write_status(
                    incident_id,
                    state="running",
                    stage=(
                        "prompt_building"
                    ),
                    detail=(
                        "Building and validating "
                        "the SLM prompt package."
                    ),
                )

                prompt = (
                    build_prompt_package(
                        evidence,
                        schema_version=(
                            phase7_settings
                            .schema_version
                        ),
                    )
                )

                write_json_atomic(
                    prompt_path(
                        incident_id
                    ),
                    prompt,
                )

                slm_result = None

                should_run_model = bool(
                    run_model
                    and phase7_settings
                    .slm_enabled
                )

                if should_run_model:
                    self._write_status(
                        incident_id,
                        state="running",
                        stage="slm_inference",
                        detail=(
                            "Calling the configured "
                            "SLM endpoint."
                        ),
                    )

                    slm_result = await (
                        run_slm(prompt)
                    )

                    write_json_atomic(
                        slm_response_path(
                            incident_id
                        ),
                        slm_result,
                    )

                final_state = (
                    "ready_for_slm"
                    if not should_run_model
                    else (
                        "complete"
                        if (
                            slm_result
                            and slm_result.get(
                                "status"
                            )
                            == "ready"
                        )
                        else "partial"
                    )
                )

                outputs = {
                    "episodeResults": (
                        episode_results
                    ),
                    "incidentAnalysisStatus": (
                        incident_analysis.get(
                            "status"
                        )
                    ),
                    "clinicalContextStatus": (
                        clinical_context.get(
                            "status"
                        )
                    ),
                    "promptMode": (
                        evidence.get(
                            "promptMode"
                        )
                    ),
                    "promptValidationStatus": (
                        prompt.get(
                            "validation",
                            {},
                        ).get(
                            "status"
                        )
                    ),
                    "slmStatus": (
                        slm_result.get(
                            "status"
                        )
                        if slm_result
                        else "not_run"
                    ),
                }

                status = self._write_status(
                    incident_id,
                    state=final_state,
                    stage=(
                        "complete"
                    ),
                    detail=(
                        "Automatic Phase 7 "
                        "processing completed."
                    ),
                    outputs=outputs,
                )

                publish_event(
                    {
                        "type": (
                            "phase7.ready"
                        ),
                        "incidentId": (
                            incident_id
                        ),
                        "episodeId": (
                            trigger_episode_id
                        ),
                        **outputs,
                    }
                )

                return {
                    "status": status,
                    "evidence": evidence,
                    "prompt": prompt,
                    "slmResponse": (
                        slm_result
                    ),
                }

            except Exception as error:
                failure = {
                    "errorType": (
                        type(error).__name__
                    ),
                    "message": str(error),
                }

                status = self._write_status(
                    incident_id,
                    state="failed",
                    stage="failed",
                    detail=(
                        "Automatic Phase 7 "
                        "processing failed."
                    ),
                    error=failure,
                )

                print(
                    "[KGEN PHASE7 ERROR]",
                    incident_id,
                    type(error).__name__,
                    str(error),
                )

                traceback.print_exc()

                publish_event(
                    {
                        "type": (
                            "phase7.failed"
                        ),
                        "incidentId": (
                            incident_id
                        ),
                        "episodeId": (
                            trigger_episode_id
                        ),
                        **failure,
                    }
                )

                return {
                    "status": status,
                    "error": failure,
                }

    def get_status(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        return read_json(
            status_path(
                incident_id
            )
        )

    def get_evidence(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        return read_json(
            evidence_path(
                incident_id
            )
        )

    def get_prompt(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        return read_json(
            prompt_path(
                incident_id
            )
        )

    def get_slm_response(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        return read_json(
            slm_response_path(
                incident_id
            )
        )


phase7_orchestrator = (
    Phase7Orchestrator()
)
