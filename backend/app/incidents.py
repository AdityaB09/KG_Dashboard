from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import settings


SEVERITY_RANK = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}


class IncidentCoordinator:
    def __init__(self) -> None:
        self.incident_path = Path(
            settings.INCIDENT_STORAGE_PATH
        )
        self.episode_path = Path(
            settings.EPISODE_STORAGE_PATH
        )

        self.incident_path.mkdir(
            parents=True,
            exist_ok=True,
        )

    def now_iso(self) -> str:
        return datetime.now(
            timezone.utc
        ).isoformat()

    def read_json(
        self,
        path: Path,
    ) -> dict[str, Any]:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def write_json(
        self,
        path: Path,
        content: dict[str, Any],
    ) -> None:
        temporary = path.with_suffix(
            ".tmp"
        )

        temporary.write_text(
            json.dumps(
                content,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        temporary.replace(path)

    def incident_file(
        self,
        incident_id: str,
    ) -> Path:
        return (
            self.incident_path
            / f"{incident_id}.json"
        )

    def episode_file(
        self,
        episode_id: str,
    ) -> Path:
        return (
            self.episode_path
            / episode_id
            / "metadata.json"
        )

    def list_incidents(
        self,
    ) -> list[dict[str, Any]]:
        incidents = []

        for path in self.incident_path.glob(
            "*.json"
        ):
            try:
                incidents.append(
                    self.read_json(path)
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                continue

        return sorted(
            incidents,
            key=lambda item: item.get(
                "updatedAt",
                "",
            ),
            reverse=True,
        )

    def get_incident(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        path = self.incident_file(
            incident_id
        )

        if not path.exists():
            raise FileNotFoundError(
                incident_id
            )

        return self.read_json(path)

    def get_latest_incident(
        self,
    ) -> dict[str, Any] | None:
        incidents = self.list_incidents()

        return (
            incidents[0]
            if incidents
            else None
        )

    def get_incident_episodes(
        self,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        incident = self.get_incident(
            incident_id
        )

        episodes = []

        for episode_id in incident.get(
            "episodeIds",
            [],
        ):
            path = self.episode_file(
                episode_id
            )

            if not path.exists():
                continue

            episodes.append(
                self.read_json(path)
            )

        return sorted(
            episodes,
            key=lambda item: item.get(
                "eventStartSeconds",
                0,
            ),
        )

    def episode_category(
        self,
        metadata: dict[str, Any],
    ) -> str:
        counts = metadata.get(
            "triggerCategoryCounts"
        ) or {}

        if counts:
            return max(
                counts,
                key=counts.get,
            )

        return "unknown_reference_annotation"

    def episode_severity(
        self,
        metadata: dict[str, Any],
    ) -> str:
        value = str(
            metadata.get("severity")
            or "info"
        )

        return (
            value
            if value in SEVERITY_RANK
            else "info"
        )

    def capture_completeness(
        self,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        existing = metadata.get(
            "captureCompleteness"
        )

        if existing:
            return existing

        requested_pre = float(
            settings.EPISODE_PRE_SECONDS
        )

        requested_post = float(
            settings.EPISODE_POST_SECONDS
        )

        actual_pre = float(
            metadata.get(
                "preSecondsCaptured",
                0,
            )
        )

        actual_post = float(
            metadata.get(
                "postSecondsCaptured",
                0,
            )
        )

        tolerance = 0.02

        return {
            "requestedPreSeconds": requested_pre,
            "actualPreSeconds": actual_pre,
            "preContextComplete": (
                actual_pre + tolerance
                >= requested_pre
            ),
            "requestedPostSeconds": requested_post,
            "actualPostSeconds": actual_post,
            "postContextComplete": (
                actual_post + tolerance
                >= requested_post
            ),
            "captureComplete": (
                actual_pre + tolerance
                >= requested_pre
                and actual_post + tolerance
                >= requested_post
            ),
            "captureTruncatedByMaxDuration": (
                float(
                    metadata.get(
                        "durationSeconds",
                        0,
                    )
                )
                + tolerance
                >= float(
                    settings
                    .EPISODE_MAX_CAPTURE_SECONDS
                )
                and actual_post + tolerance
                < requested_post
            ),
        }

    def episode_view(
        self,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "episodeId": metadata["id"],
            "display": metadata.get(
                "display"
            ),
            "severity": self.episode_severity(
                metadata
            ),
            "captureStartSeconds": float(
                metadata.get(
                    "captureStartSeconds",
                    0,
                )
            ),
            "captureEndSeconds": float(
                metadata.get(
                    "captureEndSeconds",
                    0,
                )
            ),
            "eventStartSeconds": float(
                metadata.get(
                    "eventStartSeconds",
                    0,
                )
            ),
            "eventEndSeconds": float(
                metadata.get(
                    "eventEndSeconds",
                    0,
                )
            ),
            "eventDurationSeconds": float(
                metadata.get(
                    "eventDurationSeconds",
                    0,
                )
            ),
            "triggerHeartRate": metadata.get(
                "triggerHeartRate"
            ),
            "triggerAnnotationCount": int(
                metadata.get(
                    "triggerAnnotationCount",
                    0,
                )
            ),
            "triggerAnnotationCounts": (
                metadata.get(
                    "triggerAnnotationCounts"
                )
                or {}
            ),
            "triggerCategoryCounts": (
                metadata.get(
                    "triggerCategoryCounts"
                )
                or {}
            ),
            "leadIds": metadata.get(
                "leadIds",
                [],
            ),
            "analysisStatus": metadata.get(
                "analysisStatus",
                "pending",
            ),
            "contextStatus": metadata.get(
                "contextStatus",
                "not_loaded",
            ),
            "captureCompleteness": (
                self.capture_completeness(
                    metadata
                )
            ),
            "capturedAt": metadata.get(
                "capturedAt"
            ),
        }

    def trigger_key(
        self,
        trigger: dict[str, Any],
        record: str,
        loop_number: int,
    ) -> str:
        return (
            f"{record}:"
            f"{loop_number}:"
            f"{trigger.get('absoluteSample')}:"
            f"{trigger.get('symbol')}"
        )

    def make_incident_id(
        self,
        metadata: dict[str, Any],
        category: str,
    ) -> str:
        triggers = metadata.get(
            "triggerAnnotations"
        ) or []

        if triggers:
            first_sample = min(
                int(
                    item.get(
                        "absoluteSample",
                        0,
                    )
                )
                for item in triggers
            )
        else:
            first_sample = int(
                float(
                    metadata.get(
                        "eventStartSeconds",
                        0,
                    )
                )
                * float(
                    metadata.get(
                        "sampleRate",
                        220,
                    )
                )
            )

        safe_category = "".join(
            character
            if character.isalnum()
            else "-"
            for character in category
        ).strip("-")

        demo_run_id = str(
            (
                metadata.get("oracleDemo")
                or {}
            ).get("demoRunId")
            or ""
        )

        safe_demo_run = "".join(
            character
            if character.isalnum()
            else "-"
            for character in demo_run_id
        ).strip("-")

        base_id = (
            f"inc-{metadata.get('record')}-"
            f"loop-{metadata.get('loopNumber')}-"
            f"{safe_category}-"
            f"{first_sample:09d}"
        )

        if (
            metadata.get("mode")
            == "evaluation_injection"
            and safe_demo_run
        ):
            return (
                f"{base_id}-"
                f"{safe_demo_run[-16:]}"
            )

        return base_id

    def compatible(
        self,
        incident: dict[str, Any],
        metadata: dict[str, Any],
        category: str,
    ) -> bool:
        new_demo_run_id = str(
            (
                metadata.get("oracleDemo")
                or {}
            ).get("demoRunId")
            or ""
        )

        existing_demo_run_id = str(
            incident.get("oracleDemoRunId")
            or ""
        )

        if (
            metadata.get("mode")
            == "evaluation_injection"
        ):
            # Each automatic Oracle evaluation run must
            # remain an independent incident. Re-registering
            # episodes from the same run may reuse that run's
            # incident, but a different run must never merge.
            if not new_demo_run_id:
                return False

            return (
                bool(existing_demo_run_id)
                and existing_demo_run_id
                == new_demo_run_id
            )

        if (
            incident.get("patientId")
            != metadata.get("patientId")
        ):
            return False

        if (
            incident.get("record")
            != metadata.get("record")
        ):
            return False

        if int(
            incident.get("loopNumber", 0)
        ) != int(
            metadata.get("loopNumber", 0)
        ):
            return False

        if (
            incident.get("primaryCategory")
            != category
        ):
            return False

        new_start = float(
            metadata.get(
                "eventStartSeconds",
                0,
            )
        )

        new_end = float(
            metadata.get(
                "eventEndSeconds",
                new_start,
            )
        )

        old_start = float(
            incident.get(
                "incidentStartSeconds",
                new_start,
            )
        )

        old_end = float(
            incident.get(
                "incidentEndSeconds",
                old_start,
            )
        )

        gap = float(
            settings
            .INCIDENT_MERGE_GAP_SECONDS
        )

        if (
            new_start > old_end + gap
            or new_end < old_start - gap
        ):
            return False

        combined_start = min(
            old_start,
            new_start,
        )

        combined_end = max(
            old_end,
            new_end,
        )

        return (
            combined_end - combined_start
            <= float(
                settings.INCIDENT_MAX_SECONDS
            )
        )

    def choose_primary_episode(
        self,
        views: list[dict[str, Any]],
    ) -> str | None:
        if not views:
            return None

        selected = max(
            views,
            key=lambda item: (
                int(
                    item.get(
                        "triggerAnnotationCount",
                        0,
                    )
                ),
                float(
                    item.get(
                        "eventDurationSeconds",
                        0,
                    )
                ),
                float(
                    item.get(
                        "captureCompleteness",
                        {},
                    ).get(
                        "actualPostSeconds",
                        0,
                    )
                ),
            ),
        )

        return selected["episodeId"]

    def choose_context_episode(
        self,
        views: list[dict[str, Any]],
    ) -> str | None:
        if not views:
            return None

        selected = max(
            views,
            key=lambda item: (
                bool(
                    item.get(
                        "captureCompleteness",
                        {},
                    ).get(
                        "captureComplete",
                        False,
                    )
                ),
                float(
                    item.get(
                        "captureCompleteness",
                        {},
                    ).get(
                        "actualPreSeconds",
                        0,
                    )
                )
                + float(
                    item.get(
                        "captureCompleteness",
                        {},
                    ).get(
                        "actualPostSeconds",
                        0,
                    )
                ),
                int(
                    item.get(
                        "triggerAnnotationCount",
                        0,
                    )
                ),
            ),
        )

        return selected["episodeId"]

    def recalculate(
        self,
        incident: dict[str, Any],
    ) -> dict[str, Any]:
        views = sorted(
            incident.get(
                "episodeViews",
                [],
            ),
            key=lambda item: item.get(
                "eventStartSeconds",
                0,
            ),
        )

        triggers = sorted(
            incident.get(
                "uniqueTriggerAnnotations",
                [],
            ),
            key=lambda item: item.get(
                "absoluteSample",
                0,
            ),
        )

        symbol_counts = Counter(
            str(
                item.get("symbol")
                or "unknown"
            )
            for item in triggers
        )

        category_counts = Counter(
            str(
                item.get("category")
                or "unknown"
            )
            for item in triggers
        )

        severities = [
            str(
                item.get("severity")
                or "info"
            )
            for item in triggers
        ]

        severity = max(
            severities,
            key=lambda value: (
                SEVERITY_RANK.get(
                    value,
                    0,
                )
            ),
            default="info",
        )

        if views:
            incident_start = min(
                item["eventStartSeconds"]
                for item in views
            )

            incident_end = max(
                item["eventEndSeconds"]
                for item in views
            )

            capture_start = min(
                item["captureStartSeconds"]
                for item in views
            )

            capture_end = max(
                item["captureEndSeconds"]
                for item in views
            )
        else:
            incident_start = 0
            incident_end = 0
            capture_start = 0
            capture_end = 0

        incomplete_count = sum(
            1
            for item in views
            if not item.get(
                "captureCompleteness",
                {},
            ).get(
                "captureComplete",
                False,
            )
        )

        analysis_statuses = {
            item.get(
                "analysisStatus",
                "pending",
            )
            for item in views
        }

        if (
            analysis_statuses
            and analysis_statuses
            == {"ready"}
        ):
            analysis_status = "ready"
        elif "analyzing" in analysis_statuses:
            analysis_status = "analyzing"
        else:
            analysis_status = "pending"

        category = incident.get(
            "primaryCategory",
            "reference_annotation",
        )

        display = (
            f"{category.replace('_', ' ').title()} "
            f"incident"
        )

        incident.update(
            {
                "state": "CAPTURED",
                "display": display,
                "severity": severity,
                "incidentStartSeconds": round(
                    incident_start,
                    3,
                ),
                "incidentEndSeconds": round(
                    incident_end,
                    3,
                ),
                "durationSeconds": round(
                    incident_end
                    - incident_start,
                    3,
                ),
                "captureStartSeconds": round(
                    capture_start,
                    3,
                ),
                "captureEndSeconds": round(
                    capture_end,
                    3,
                ),
                "captureSpanSeconds": round(
                    capture_end
                    - capture_start,
                    3,
                ),
                "episodeViews": views,
                "episodeIds": [
                    item["episodeId"]
                    for item in views
                ],
                "episodeCount": len(views),
                "primaryEpisodeId": (
                    self.choose_primary_episode(
                        views
                    )
                ),
                "bestContextEpisodeId": (
                    self.choose_context_episode(
                        views
                    )
                ),
                "uniqueTriggerAnnotations": (
                    triggers
                ),
                "uniqueTriggerCount": len(
                    triggers
                ),
                "triggerAnnotationCounts": dict(
                    symbol_counts
                ),
                "triggerCategoryCounts": dict(
                    category_counts
                ),
                "incompleteEpisodeCount": (
                    incomplete_count
                ),
                "completeEpisodeCount": (
                    len(views)
                    - incomplete_count
                ),
                "analysisStatus": (
                    analysis_status
                ),
                "contextStatus": (
                    incident.get(
                        "contextStatus",
                        "not_loaded",
                    )
                ),
                "summaryStatus": (
                    incident.get(
                        "summaryStatus",
                        "not_started",
                    )
                ),
                "updatedAt": self.now_iso(),
            }
        )

        return incident

    def register_episode(
        self,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        category = self.episode_category(
            metadata
        )

        existing = next(
            (
                item
                for item
                in self.list_incidents()
                if self.compatible(
                    item,
                    metadata,
                    category,
                )
            ),
            None,
        )

        if existing is None:
            incident_id = self.make_incident_id(
                metadata,
                category,
            )

            existing = {
                "schemaVersion": "incident-v1",
                "id": incident_id,
                "patientId": metadata.get(
                    "patientId"
                ),
                "mode": (
                    metadata.get("mode")
                    or "research"
                ),
                "oracleDemoRunId": (
                    (
                        metadata.get(
                            "oracleDemo"
                        )
                        or {}
                    ).get("demoRunId")
                ),
                "record": metadata.get(
                    "record"
                ),
                "loopNumber": metadata.get(
                    "loopNumber"
                ),
                "primaryCategory": category,
                "episodeViews": [],
                "uniqueTriggerAnnotations": [],
                "analysisStatus": "pending",
                "contextStatus": "not_loaded",
                "summaryStatus": "not_started",
                "provenance": {
                    "waveformSource": (
                        "PhysioNet INCART"
                    ),
                    "triggerSource": (
                        "INCART atr "
                        "reference annotations"
                    ),
                    "clinicalContextSource": None,
                },
                "createdAt": self.now_iso(),
            }

        episode_id = metadata["id"]

        existing["episodeViews"] = [
            item
            for item in existing.get(
                "episodeViews",
                [],
            )
            if item.get("episodeId")
            != episode_id
        ]

        existing["episodeViews"].append(
            self.episode_view(metadata)
        )

        trigger_map = {
            self.trigger_key(
                item,
                str(existing.get("record")),
                int(
                    existing.get(
                        "loopNumber",
                        0,
                    )
                ),
            ): item
            for item in existing.get(
                "uniqueTriggerAnnotations",
                [],
            )
        }

        for trigger in metadata.get(
            "triggerAnnotations",
            [],
        ):
            key = self.trigger_key(
                trigger,
                str(metadata.get("record")),
                int(
                    metadata.get(
                        "loopNumber",
                        0,
                    )
                ),
            )

            trigger_map[key] = trigger

        existing[
            "uniqueTriggerAnnotations"
        ] = list(
            trigger_map.values()
        )

        incident = self.recalculate(
            existing
        )

        self.write_json(
            self.incident_file(
                incident["id"]
            ),
            incident,
        )

        return incident

    def rebuild_from_episodes(
        self,
    ) -> dict[str, Any]:
        for path in self.incident_path.glob(
            "*.json"
        ):
            path.unlink()

        episodes = []

        for path in self.episode_path.glob(
            "*/metadata.json"
        ):
            try:
                episodes.append(
                    (
                        path,
                        self.read_json(path),
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                continue

        episodes.sort(
            key=lambda item: (
                str(
                    item[1].get(
                        "record",
                        "",
                    )
                ),
                int(
                    item[1].get(
                        "loopNumber",
                        0,
                    )
                ),
                float(
                    item[1].get(
                        "eventStartSeconds",
                        0,
                    )
                ),
            )
        )

        for path, metadata in episodes:
            incident = self.register_episode(
                metadata
            )

            metadata["incidentId"] = (
                incident["id"]
            )

            metadata[
                "incidentPrimaryEpisodeId"
            ] = incident.get(
                "primaryEpisodeId"
            )

            metadata[
                "incidentBestContextEpisodeId"
            ] = incident.get(
                "bestContextEpisodeId"
            )

            self.write_json(
                path,
                metadata,
            )

        incidents = self.list_incidents()

        return {
            "episodeCount": len(episodes),
            "incidentCount": len(incidents),
            "incidents": incidents,
        }

    def optional_json(
        self,
        path: Path,
    ) -> dict[str, Any]:
        if not path.exists():
            return {}

        try:
            return self.read_json(path)
        except (
            OSError,
            json.JSONDecodeError,
        ):
            return {}

    def unique_items(
        self,
        values: list[Any],
    ) -> list[Any]:
        output = []
        seen = set()

        for value in values:
            key = json.dumps(
                value,
                sort_keys=True,
                default=str,
            )

            if key in seen:
                continue

            seen.add(key)
            output.append(value)

        return output

    def build_slm_context(
    self,
    incident_id: str,
) -> dict[str, Any]:
        incident = self.get_incident(
            incident_id
        )

        episodes = self.get_incident_episodes(
            incident_id
        )

        analysis_results = []
        clinical_contexts = []

        for episode in episodes:
            episode_dir = (
                self.episode_path
                / episode["id"]
            )

            analysis = self.optional_json(
                episode_dir
                / "analysis.json"
            )

            context = self.optional_json(
                episode_dir
                / "clinical_context.json"
            )

            if analysis:
                analysis_results.append(
                    {
                        "episodeId": (
                            episode["id"]
                        ),
                        **analysis,
                    }
                )

            if context:
                clinical_contexts.append(
                    {
                        "episodeId": (
                            episode["id"]
                        ),
                        **context,
                    }
                )

        lead_ids = sorted(
            {
                lead_id
                for episode in episodes
                for lead_id in episode.get(
                    "leadIds",
                    [],
                )
            }
        )

        heart_rates = [
            int(
                episode[
                    "triggerHeartRate"
                ]
            )
            for episode in episodes
            if episode.get(
                "triggerHeartRate"
            )
            is not None
        ]

        labs = self.unique_items(
            [
                item
                for context
                in clinical_contexts
                for item in context.get(
                    "labTrends",
                    [],
                )
            ]
        )

        medications = self.unique_items(
            [
                item
                for context
                in clinical_contexts
                for item in context.get(
                    "medicationTimeline",
                    [],
                )
            ]
        )

        conditions = self.unique_items(
    [
        item
        for context
        in clinical_contexts
        for item in context.get(
            "conditions",
            [],
        )
    ]
)
        ready_clinical_contexts = [
            context
            for context in clinical_contexts
            if context.get("status") == "ready"
        ]

        primary_clinical_context = (
            ready_clinical_contexts[-1]
            if ready_clinical_contexts
            else (
                clinical_contexts[-1]
                if clinical_contexts
                else {}
            )
        )

        raw_patient_summary = (
            primary_clinical_context.get(
                "patientSummary",
                {},
            )
            or {}
        )

        slm_patient_summary = {
            key: raw_patient_summary.get(key)
            for key in (
                "ageAtContextAnchor",
                "gender",
                "deceased",
                "maritalStatus",
                "languages",
            )
            if raw_patient_summary.get(key)
            is not None
        }

        clinical_vital_trends = (
            primary_clinical_context.get(
                "vitalTrends",
                [],
            )
            or []
        )

        clinical_encounters = (
            primary_clinical_context.get(
                "encounters",
                [],
            )
            or []
        )

        clinical_diagnostic_reports = (
            primary_clinical_context.get(
                "diagnosticReports",
                [],
            )
            or []
        )

        clinical_documents = (
            primary_clinical_context.get(
                "documents",
                [],
            )
            or []
        )

        clinical_data_quality = (
            primary_clinical_context.get(
                "dataQuality",
                {},
            )
            or {}
        )

        clinical_limitations = (
            primary_clinical_context.get(
                "limitations",
                [],
            )
            or []
        )
        quality_results = [
            {
                "episodeId": item[
                    "episodeId"
                ],
                "value": item.get(
                    "signalQuality"
                ),
            }
            for item in analysis_results
            if item.get("signalQuality")
        ]

        morphology_results = [
            {
                "episodeId": item[
                    "episodeId"
                ],
                "value": item.get(
                    "morphology"
                ),
            }
            for item in analysis_results
            if item.get("morphology")
        ]

        missing_signals = [
            "ppg",
            "spo2",
            "respiration",
            "temperature",
            "arterial_blood_pressure",
        ]

        limitations = [
            # (
            #     "The incident was triggered from "
            #     "INCART reference annotations and "
            #     "is not an independent diagnosis."
            # ),
            # (
            #     "Raw ECG arrays remain in waveform "
            #     "storage and are not included in "
            #     "the SLM input."
            # ),
        ]

        if not morphology_results:
            limitations.append(
                "Deterministic morphology analysis is pending."
            )

        if not labs and not medications:
            limitations.append(
                "Episode-linked clinical context is not loaded."
            )

        if incident.get(
            "incompleteEpisodeCount",
            0,
        ):
            limitations.append(
                (
                    f"{incident['incompleteEpisodeCount']} "
                    "episode view(s) contain incomplete "
                    "requested pre-event or post-event context."
                )
            )

        evidence_candidates = [
            {
                "type": (
                    "dataset_reference_annotation"
                ),
                "source": "INCART atr",
                "finding": incident.get(
                    "display"
                ),
                "triggerCounts": incident.get(
                    "triggerAnnotationCounts",
                    {},
                ),
                "uniqueTriggerCount": (
                    incident.get(
                        "uniqueTriggerCount",
                        0,
                    )
                ),
            },
            {
                "type": "capture_completeness",
                "episodeCount": incident.get(
                    "episodeCount",
                    0,
                ),
                "completeEpisodeCount": (
                    incident.get(
                        "completeEpisodeCount",
                        0,
                    )
                ),
                "incompleteEpisodeCount": (
                    incident.get(
                        "incompleteEpisodeCount",
                        0,
                    )
                ),
            },
        ]

        return {
            "schemaVersion": "slm-context-v1",
            "incidentId": incident["id"],
            "mode": incident.get(
                "mode",
                "research",
            ),
            "episodeAnnotation": {
                "display": incident.get(
                    "display"
                ),
                "category": incident.get(
                    "primaryCategory"
                ),
                "severity": incident.get(
                    "severity"
                ),
                "sourceType": (
                    "dataset_reference_annotation"
                ),
                "sourceName": (
                    "PhysioNet INCART"
                ),
                "incidentStartSeconds": (
                    incident.get(
                        "incidentStartSeconds"
                    )
                ),
                "incidentEndSeconds": (
                    incident.get(
                        "incidentEndSeconds"
                    )
                ),
                "durationSeconds": (
                    incident.get(
                        "durationSeconds"
                    )
                ),
                "episodeCount": incident.get(
                    "episodeCount"
                ),
                "primaryEpisodeId": (
                    incident.get(
                        "primaryEpisodeId"
                    )
                ),
                "bestContextEpisodeId": (
                    incident.get(
                        "bestContextEpisodeId"
                    )
                ),
                "triggerCounts": (
                    incident.get(
                        "triggerAnnotationCounts",
                        {},
                    )
                ),
                "uniqueTriggerCount": (
                    incident.get(
                        "uniqueTriggerCount",
                        0,
                    )
                ),
            },
            "signalQuality": {
                "status": (
                    "ready"
                    if quality_results
                    else "pending"
                ),
                "episodeResults": (
                    quality_results
                ),
            },
            "morphology": {
                "status": (
                    "ready"
                    if morphology_results
                    else "pending"
                ),
                "episodeResults": (
                    morphology_results
                ),
            },
            "availableSignals": {
                "ecg": True,
                "ecgLeadIds": lead_ids,
                "episodeViews": [
                    item.get("id")
                    for item in episodes
                ],
                "rawWaveformsStored": True,
            },
            "missingSignals": missing_signals,
            "vitalResponse": {
                "triggerHeartRates": (
                    heart_rates
                ),
                "minimumTriggerHeartRate": (
                    min(heart_rates)
                    if heart_rates
                    else None
                ),
                "maximumTriggerHeartRate": (
                    max(heart_rates)
                    if heart_rates
                    else None
                ),
                "averageTriggerHeartRate": (
                    round(
                        mean(heart_rates),
                        1,
                    )
                    if heart_rates
                    else None
                ),
            },
            "patientSummary": slm_patient_summary,

"labTrends": labs,

"vitalTrends": (
    clinical_vital_trends
),

"medicationTimeline": medications,

"conditions": conditions,

"encounters": (
    clinical_encounters[:10]
),

"diagnosticReports": (
    clinical_diagnostic_reports[:10]
),

"documentMetadata": (
    clinical_documents[:10]
),

"clinicalDataQuality": (
    clinical_data_quality
),

"clinicalContextLimitations": (
    clinical_limitations
),
            "evidenceCandidates": (
                evidence_candidates
            ),
            "limitations": limitations,
            "analysisStatus": incident.get(
                "analysisStatus",
                "pending",
            ),
            "contextStatus": incident.get(
                "contextStatus",
                "not_loaded",
            ),
            "provenance": incident.get(
                "provenance",
                {},
            ),
        }


incident_coordinator = IncidentCoordinator()