from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.fhir_http import bundle_resources
from app.incidents import incident_coordinator
from app.normalizer import (
    FIELD_LABELS,
    LOINC,
    classify_field,
    clinically_plausible,
    get_codes,
    get_observation_timestamp,
    get_quantity_unit,
    get_quantity_value,
)
from app.providers import (
    fetch_oracle_observations_by_codes,
    fetch_oracle_patient,
    fetch_oracle_patient_resources,
    fetch_provider_medications,
    fetch_provider_observations,
    merge_fhir_bundles,
    fetch_oracle_paginated_observations,

)


LAB_FIELDS = (
    "glucose",
    "potassium",
    "creatinine",
    "wbc",
)

VITAL_FIELDS = (
    "heartRate",
    "respiratoryRate",
    "spo2",
    "temperature",
)


def parse_datetime(
    value: Any,
) -> datetime | None:
    if not value:
        return None

    text = str(value).strip().replace(
        "Z",
        "+00:00",
    )

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def codeable_text(
    value: Any,
) -> str | None:
    if not isinstance(value, dict):
        return None

    if value.get("text"):
        return str(value["text"])

    for coding in value.get(
        "coding",
        [],
    ) or []:
        if coding.get("display"):
            return str(coding["display"])

        if coding.get("code"):
            return str(coding["code"])

    return None


def relation_to_anchor(
    value: datetime | None,
    anchor: datetime,
) -> dict[str, Any]:
    if value is None:
        return {
            "minutesFromAnchor": None,
            "relation": "unknown",
            "relationLabel": "Time unavailable",
        }

    minutes = round(
        (value - anchor).total_seconds() / 60,
        1,
    )

    if abs(minutes) < 1:
        label = "At context snapshot"
        relation = "at_anchor"
    elif minutes < 0:
        label = f"{abs(minutes):g} min before"
        relation = "before_anchor"
    else:
        label = f"{minutes:g} min after"
        relation = "after_anchor"

    return {
        "minutesFromAnchor": minutes,
        "relation": relation,
        "relationLabel": label,
    }


def observation_candidates(
    observation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        observation,
        *(
            observation.get(
                "component",
                [],
            )
            or []
        ),
    ]


def extract_observation_points(
    bundle: dict[str, Any],
    anchor: datetime,
) -> dict[str, list[dict[str, Any]]]:
    output = {
        field: []
        for field in (
            *LAB_FIELDS,
            *VITAL_FIELDS,
        )
    }

    observations = bundle_resources(
        bundle,
        "Observation",
    )

    for observation in observations:
        timestamp_text = (
            get_observation_timestamp(
                observation
            )
        )

        observed_at = parse_datetime(
            timestamp_text
        )

        for candidate in observation_candidates(
            observation
        ):
            codes = get_codes(
                candidate.get("code")
            )

            value = get_quantity_value(
                candidate
            )

            unit = get_quantity_unit(
                candidate
            )

            if value is None:
                continue

            for field in output:
                if not codes.intersection(
                    set(LOINC[field])
                ):
                    continue

                if not clinically_plausible(
                    field,
                    value,
                    unit,
                ):
                    continue

                relation = relation_to_anchor(
                    observed_at,
                    anchor,
                )

                output[field].append(
                    {
                        "resourceId": (
                            observation.get("id")
                        ),
                        "value": value,
                        "unit": unit,
                        "observedAt": (
                            observed_at.isoformat()
                            if observed_at
                            else timestamp_text
                        ),
                        "status": observation.get(
                            "status"
                        ),
                        "codes": sorted(codes),
                        **relation,
                        "_sortTime": (
                            observed_at.timestamp()
                            if observed_at
                            else 0
                        ),
                    }
                )

    for field, points in output.items():
        points.sort(
            key=lambda item: item[
                "_sortTime"
            ]
        )

        for point in points:
            point.pop(
                "_sortTime",
                None,
            )

        output[field] = points[-12:]

    return output


def trend_direction(
    points: list[dict[str, Any]],
) -> str:
    if len(points) < 2:
        return "insufficient_data"

    previous = float(
        points[-2]["value"]
    )

    latest = float(
        points[-1]["value"]
    )

    threshold = max(
        abs(previous) * 0.03,
        0.01,
    )

    difference = latest - previous

    if difference > threshold:
        return "rising"

    if difference < -threshold:
        return "falling"

    return "stable"


def build_trends(
    points_by_field: dict[
        str,
        list[dict[str, Any]],
    ],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    trends = []

    for field in fields:
        points = points_by_field.get(
            field,
            [],
        )

        if not points:
            continue

        latest = points[-1]

        trends.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(
                    field,
                    field,
                ),
                "loincCodes": LOINC[field],
                "latestValue": latest["value"],
                "unit": latest.get("unit"),
                "latestAt": latest.get(
                    "observedAt"
                ),
                "latestRelation": latest.get(
                    "relation"
                ),
                "latestRelationLabel": (
                    latest.get(
                        "relationLabel"
                    )
                ),
                "trendDirection": (
                    trend_direction(points)
                ),
                "color": classify_field(
                    field,
                    latest["value"],
                ),
                "points": points,
            }
        )

    return trends


def medication_name(
    resource: dict[str, Any],
) -> str:
    return (
        codeable_text(
            resource.get(
                "medicationCodeableConcept"
            )
        )
        or (
            resource.get(
                "medicationReference"
            )
            or {}
        ).get("display")
        or resource.get("id")
        or "Medication"
    )


def medication_time(
    resource: dict[str, Any],
) -> Any:
    resource_type = resource.get(
        "resourceType"
    )

    if resource_type == "MedicationAdministration":
        return (
            resource.get(
                "effectiveDateTime"
            )
            or (
                resource.get(
                    "effectivePeriod"
                )
                or {}
            ).get("start")
        )

    if resource_type == "MedicationRequest":
        return resource.get("authoredOn")

    if resource_type == "MedicationDispense":
        return (
            resource.get(
                "whenHandedOver"
            )
            or resource.get(
                "whenPrepared"
            )
        )

    return None


def medication_dosage(
    resource: dict[str, Any],
) -> dict[str, Any]:
    resource_type = resource.get(
        "resourceType"
    )

    if resource_type == "MedicationAdministration":
        dosage = resource.get(
            "dosage"
        ) or {}

        dose = dosage.get("dose") or {}

        return {
            "doseValue": dose.get("value"),
            "doseUnit": (
                dose.get("unit")
                or dose.get("code")
            ),
            "route": codeable_text(
                dosage.get("route")
            ),
            "instructions": dosage.get(
                "text"
            ),
        }

    instructions = (
        resource.get(
            "dosageInstruction",
            [],
        )
        or [{}]
    )[0]

    dose_and_rate = (
        instructions.get(
            "doseAndRate",
            [],
        )
        or [{}]
    )[0]

    dose = (
        dose_and_rate.get(
            "doseQuantity"
        )
        or {}
    )

    return {
        "doseValue": dose.get("value"),
        "doseUnit": (
            dose.get("unit")
            or dose.get("code")
        ),
        "route": codeable_text(
            instructions.get("route")
        ),
        "instructions": (
            instructions.get("text")
            or (
                instructions.get("timing")
                or {}
            ).get("code", {}).get("text")
        ),
    }


def medication_evidence(
    resource_type: str,
) -> str:
    if resource_type == "MedicationAdministration":
        return "administration"

    if resource_type == "MedicationDispense":
        return "dispense"

    if resource_type == "MedicationRequest":
        return "order"

    return "unknown"


def build_medication_timeline(
    resources: list[dict[str, Any]],
    anchor: datetime,
) -> list[dict[str, Any]]:
    output = []

    for resource in resources:
        resource_type = str(
            resource.get("resourceType")
            or "Medication"
        )

        time_text = medication_time(
            resource
        )

        event_time = parse_datetime(
            time_text
        )

        dosage = medication_dosage(
            resource
        )

        dose_value = dosage.get(
            "doseValue"
        )

        dose_unit = dosage.get(
            "doseUnit"
        )

        dose_display = (
            f"{dose_value:g} {dose_unit}"
            if isinstance(
                dose_value,
                (int, float),
            )
            and dose_unit
            else (
                dosage.get("instructions")
                or "Dose unavailable"
            )
        )

        output.append(
            {
                "id": resource.get("id"),
                "name": medication_name(
                    resource
                ),
                "resourceType": resource_type,
                "status": resource.get(
                    "status"
                ),
                "evidenceLevel": (
                    medication_evidence(
                        resource_type
                    )
                ),
                "doseValue": dose_value,
                "doseUnit": dose_unit,
                "doseDisplay": dose_display,
                "route": dosage.get("route"),
                "instructions": dosage.get(
                    "instructions"
                ),
                "eventTime": (
                    event_time.isoformat()
                    if event_time
                    else time_text
                ),
                **relation_to_anchor(
                    event_time,
                    anchor,
                ),
            }
        )

    output.sort(
        key=lambda item: (
            item.get("eventTime") or ""
        ),
        reverse=True,
    )

    return output[:30]


def build_conditions(
    resources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []

    for resource in resources:
        output.append(
            {
                "id": resource.get("id"),
                "name": (
                    codeable_text(
                        resource.get("code")
                    )
                    or "Condition"
                ),
                "clinicalStatus": (
                    codeable_text(
                        resource.get(
                            "clinicalStatus"
                        )
                    )
                ),
                "verificationStatus": (
                    codeable_text(
                        resource.get(
                            "verificationStatus"
                        )
                    )
                ),
                "onset": (
                    resource.get(
                        "onsetDateTime"
                    )
                    or resource.get(
                        "recordedDate"
                    )
                ),
            }
        )

    return output[:30]


def build_encounters(
    resources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []

    for resource in resources:
        encounter_class = (
            resource.get("class")
            or {}
        )

        encounter_type = (
            resource.get("type", [])
            or [{}]
        )[0]

        period = resource.get(
            "period"
        ) or {}

        output.append(
            {
                "id": resource.get("id"),
                "status": resource.get(
                    "status"
                ),
                "class": (
                    encounter_class.get(
                        "display"
                    )
                    or encounter_class.get(
                        "code"
                    )
                ),
                "type": codeable_text(
                    encounter_type
                ),
                "start": period.get("start"),
                "end": period.get("end"),
            }
        )

    return output[:20]


class ClinicalContextService:
    def context_path(
        self,
        incident_id: str,
    ) -> Path:
        incident = (
            incident_coordinator
            .get_incident(incident_id)
        )

        episode_id = (
            incident.get(
                "bestContextEpisodeId"
            )
            or incident.get(
                "primaryEpisodeId"
            )
        )

        if not episode_id:
            raise FileNotFoundError(
                incident_id
            )

        return (
            Path(
                settings.EPISODE_STORAGE_PATH
            )
            / episode_id
            / "clinical_context.json"
        )

    def get(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        path = self.context_path(
            incident_id
        )

        if not path.exists():
            return {
                "schemaVersion": (
                    "clinical-context-v1"
                ),
                "incidentId": incident_id,
                "status": "not_loaded",
                "patientSummary": {},
                "labTrends": [],
                "vitalTrends": [],
                "medicationTimeline": [],
                "conditions": [],
                "encounters": [],
                "diagnosticReports": [],
                "documents": [],
                "dataQuality": {
                    "fallbackUsed": False,
                    "observationCount": 0,
                    "targetedObservationCount": 0,
                    "patientLoaded": False,
                    "diagnosticReportCount": 0,
                    "documentCount": 0,
                },
            }

        return incident_coordinator.read_json(
            path
        )

    def anchor(
        self,
        incident: dict[str, Any],
        episodes: list[dict[str, Any]],
    ) -> tuple[datetime, str]:
        explicit = parse_datetime(
            incident.get("eventDateTime")
        )

        if explicit:
            return (
                explicit,
                "incident_event_datetime",
            )

        selected_id = (
            incident.get(
                "bestContextEpisodeId"
            )
            or incident.get(
                "primaryEpisodeId"
            )
        )

        selected = next(
            (
                episode
                for episode in episodes
                if episode.get("id")
                == selected_id
            ),
            episodes[0] if episodes else {},
        )

        captured_at = parse_datetime(
            selected.get("capturedAt")
            or incident.get("updatedAt")
        )

        return (
            captured_at
            or datetime.now(timezone.utc),
            "capture_time_proxy",
        )

    def save(
        self,
        incident_id: str,
        context: dict[str, Any],
    ) -> None:
        path = self.context_path(
            incident_id
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        incident_coordinator.write_json(
            path,
            context,
        )

        incident = (
            incident_coordinator
            .get_incident(incident_id)
        )

        incident["contextStatus"] = (
            context["status"]
        )

        incident[
            "clinicalContextEpisodeId"
        ] = context.get(
            "storedWithEpisodeId"
        )

        incident.setdefault(
            "provenance",
            {},
        )[
            "clinicalContextSource"
        ] = context.get(
            "provenance",
            {},
        ).get("source")

        incident["updatedAt"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        incident_coordinator.write_json(
            incident_coordinator
            .incident_file(incident_id),
            incident,
        )

    async def load(
        self,
        *,
        incident_id: str,
        patient_id: str | None,
        access_token: str | None,
        fhir_base_url: str | None,
    ) -> dict[str, Any]:
        incident = (
            incident_coordinator
            .get_incident(incident_id)
        )

        episodes = (
            incident_coordinator
            .get_incident_episodes(
                incident_id
            )
        )

        selected_episode_id = (
            incident.get(
                "bestContextEpisodeId"
            )
            or incident.get(
                "primaryEpisodeId"
            )
        )

        anchor, anchor_basis = (
            self.anchor(
                incident,
                episodes,
            )
        )

        limitations = []

        if incident.get("mode") == "research":
            limitations.append(
                "INCART waveform and FHIR context are a controlled research pairing, not verified same-patient clinical data."
            )

            if not settings.ALLOW_RESEARCH_FHIR_PAIRING:
                context = {
                    "schemaVersion": (
                        "clinical-context-v1"
                    ),
                    "incidentId": incident_id,
                    "status": "unavailable",
                    "storedWithEpisodeId": (
                        selected_episode_id
                    ),
                    "contextAnchor": {
                        "value": (
                            anchor.isoformat()
                        ),
                        "basis": anchor_basis,
                    },
                    "patientSummary": {},
                    "labTrends": [],
                    "vitalTrends": [],
                    "medicationTimeline": [],
                    "conditions": [],
                    "encounters": [],
                    "diagnosticReports": [],
                    "documents": [],
                    "dataQuality": {
                        "fallbackUsed": False,
                        "observationCount": 0,
                        "targetedObservationCount": 0,
                        "patientLoaded": False,
                        "diagnosticReportCount": 0,
                        "documentCount": 0,
                    },
                    "limitations": [
                        *limitations,
                        "Research FHIR pairing is disabled.",
                    ],
                    "provenance": {
                        "source": None,
                    },
                }

                self.save(
                    incident_id,
                    context,
                )

                return context

        if not patient_id:
            context = {
                "schemaVersion": (
                    "clinical-context-v1"
                ),
                "incidentId": incident_id,
                "status": "unavailable",
                "storedWithEpisodeId": (
                    selected_episode_id
                ),
                "contextAnchor": {
                    "value": anchor.isoformat(),
                    "basis": anchor_basis,
                },
                "patientSummary": {},
                "labTrends": [],
                "vitalTrends": [],
                "medicationTimeline": [],
                "conditions": [],
                "encounters": [],
                "diagnosticReports": [],
                "documents": [],
                "dataQuality": {
                    "fallbackUsed": False,
                    "observationCount": 0,
                    "targetedObservationCount": 0,
                    "patientLoaded": False,
                    "diagnosticReportCount": 0,
                    "documentCount": 0,
                },
                "limitations": [
                    *limitations,
                    "No Oracle SMART patient context or configured test patient was available.",
                ],
                "provenance": {
                    "source": None,
                },
            }

            self.save(
                incident_id,
                context,
            )

            return context

        targeted_lab_codes = {
    field: list(LOINC[field])
    for field in (
        "glucose",
        "creatinine",
        "wbc",
    )
}

         

        (
            general_observation_bundle,
            targeted_lab_bundle,
            laboratory_observation_bundle,
            medications,
            patient_resource,
            conditions,
            encounters,
            diagnostic_reports,
            documents,
        ) = await asyncio.gather(
            fetch_provider_observations(
                settings.CLINICAL_CONTEXT_PROVIDER,
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            ),

            safely_fetch_targeted_labs(
                patient_id,
                targeted_lab_codes,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            ),

            fetch_oracle_paginated_observations(
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                category="laboratory",
                count=100,
                max_pages=10,
            ),

            fetch_provider_medications(
                settings.CLINICAL_CONTEXT_PROVIDER,
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            ),

            fetch_oracle_patient(
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            ),

            fetch_oracle_patient_resources(
                "Condition",
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=(
                    settings
                    .CLINICAL_CONTEXT_RESOURCE_COUNT
                ),
            ),

            fetch_oracle_patient_resources(
                "Encounter",
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=(
                    settings
                    .CLINICAL_CONTEXT_RESOURCE_COUNT
                ),
            ),

            fetch_oracle_patient_resources(
                "DiagnosticReport",
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=(
                    settings
                    .CLINICAL_CONTEXT_RESOURCE_COUNT
                ),
            ),

            fetch_oracle_patient_resources(
                "DocumentReference",
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=(
                    settings
                    .CLINICAL_CONTEXT_RESOURCE_COUNT
                ),
            ),
        )

        observation_bundle = merge_fhir_bundles(
    general_observation_bundle,
    targeted_lab_bundle,
    laboratory_observation_bundle,
)

        points = extract_observation_points(
            observation_bundle,
            anchor,
        )

        lab_trends = build_trends(
            points,
            LAB_FIELDS,
        )

        vital_trends = build_trends(
            points,
            VITAL_FIELDS,
        )

        medication_timeline = (
            build_medication_timeline(
                medications,
                anchor,
            )
        )

        condition_rows = build_conditions(
            conditions
        )

        encounter_rows = build_encounters(
            encounters
        )

        patient_summary = build_patient_summary(
            patient_resource,
            anchor,
        )

        diagnostic_report_rows = (
            build_diagnostic_reports(
                diagnostic_reports
            )
        )

        document_rows = (
            build_document_references(
                documents
            )
        )

        has_context = any(
            (
                bool(patient_resource),
                lab_trends,
                vital_trends,
                medication_timeline,
                condition_rows,
                encounter_rows,
                diagnostic_report_rows,
                document_rows,
            )
        )

        if anchor_basis == "capture_time_proxy":
            limitations.append(
                "FHIR timing is relative to the episode capture timestamp because the INCART record has no verified clinical event datetime."
            )

        context = {
            "schemaVersion": (
                "clinical-context-v1"
            ),
            "incidentId": incident_id,
            "status": (
                "ready"
                if has_context
                else "empty"
            ),
            "storedWithEpisodeId": (
                selected_episode_id
            ),
            "waveformPatientId": (
                incident.get("patientId")
            ),
            "fhirPatientId": patient_id,
            "linkageMode": (
                "controlled_research_pairing"
                if incident.get("mode")
                == "research"
                else "patient_linked"
            ),
            "contextAnchor": {
                "value": anchor.isoformat(),
                "basis": anchor_basis,
            },
            "patientSummary": patient_summary,
            "labTrends": lab_trends,
            "vitalTrends": vital_trends,
            "medicationTimeline": (
                medication_timeline
            ),
            "conditions": condition_rows,
            "encounters": encounter_rows,
            "diagnosticReports": (
                diagnostic_report_rows
            ),
            "documents": document_rows,
            "dataQuality": {
                "fallbackUsed": False,
                "observationCount": len(
                    bundle_resources(
                        observation_bundle,
                        "Observation",
                    )
                ),
                "laboratoryObservationCount": len(
    bundle_resources(
        laboratory_observation_bundle,
        "Observation",
    )
),

"laboratoryPagesFetched": (
    laboratory_observation_bundle.get(
        "pagesFetched",
        0,
    )
),
                "targetedObservationCount": len(
                    bundle_resources(
                        targeted_lab_bundle,
                        "Observation",
                    )
                ),
                "matchedLabCount": sum(
                    len(item["points"])
                    for item in lab_trends
                ),
                "matchedVitalCount": sum(
                    len(item["points"])
                    for item in vital_trends
                ),
                "medicationCount": len(
                    medication_timeline
                ),
                "conditionCount": len(
                    condition_rows
                ),
                "encounterCount": len(
                    encounter_rows
                ),
                "patientLoaded": bool(
                    patient_resource
                ),
                "diagnosticReportCount": len(
                    diagnostic_reports
                ),
                "documentCount": len(
                    documents
                ),
                "laboratoryObservationCount": len(
                    bundle_resources(
                        laboratory_observation_bundle,
                        "Observation",
                    )
                ),
            },
            "limitations": limitations,
            "provenance": {
                "source": "Oracle SMART on FHIR",
                "provider": (
                    settings
                    .CLINICAL_CONTEXT_PROVIDER
                ),
                "fhirBaseUrl": fhir_base_url,
                "loadedAt": datetime.now(
                    timezone.utc
                ).isoformat(),
                "usesFallbackDemoValues": False,
            },
        }

        self.save(
            incident_id,
            context,
        )

        return context


def build_patient_summary(
    patient: dict[str, Any],
    anchor: datetime,
) -> dict[str, Any]:
    birth_date_text = patient.get(
        "birthDate"
    )

    birth_date = None

    if birth_date_text:
        try:
            birth_date = datetime.fromisoformat(
                birth_date_text
            ).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            birth_date = None

    age = None

    if birth_date:
        age = (
            anchor.year
            - birth_date.year
            - (
                (
                    anchor.month,
                    anchor.day,
                )
                <
                (
                    birth_date.month,
                    birth_date.day,
                )
            )
        )

    languages = []

    for item in (
        patient.get(
            "communication",
            [],
        )
        or []
    ):
        language = codeable_text(
            item.get("language")
        )

        if language:
            languages.append(language)

    return {
        "gender": patient.get("gender"),
        "birthDate": birth_date_text,
        "ageAtContextAnchor": age,
        "deceased": (
            patient.get("deceasedBoolean")
            if "deceasedBoolean" in patient
            else patient.get(
                "deceasedDateTime"
            )
        ),
        "maritalStatus": codeable_text(
            patient.get(
                "maritalStatus"
            )
        ),
        "languages": sorted(
            set(languages)
        ),
    }


def build_diagnostic_reports(
    resources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []

    for resource in resources:
        effective = (
            resource.get(
                "effectiveDateTime"
            )
            or (
                resource.get(
                    "effectivePeriod"
                )
                or {}
            ).get("start")
            or resource.get("issued")
        )

        output.append(
            {
                "id": resource.get("id"),
                "status": resource.get(
                    "status"
                ),
                "category": [
                    codeable_text(item)
                    for item in (
                        resource.get(
                            "category",
                            [],
                        )
                        or []
                    )
                    if codeable_text(item)
                ],
                "name": codeable_text(
                    resource.get("code")
                ),
                "effectiveAt": effective,
                "issuedAt": resource.get(
                    "issued"
                ),
                "conclusion": resource.get(
                    "conclusion"
                ),
                "conclusionCodes": [
                    codeable_text(item)
                    for item in (
                        resource.get(
                            "conclusionCode",
                            [],
                        )
                        or []
                    )
                    if codeable_text(item)
                ],
                "resultReferences": [
                    item.get("reference")
                    for item in (
                        resource.get(
                            "result",
                            [],
                        )
                        or []
                    )
                    if item.get("reference")
                ],
            }
        )

    return output[:30]



def build_document_references(
    resources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []

    for resource in resources:
        content = []

        for item in (
            resource.get("content", [])
            or []
        ):
            attachment = item.get(
                "attachment",
                {},
            ) or {}

            content.append(
                {
                    "contentType": (
                        attachment.get(
                            "contentType"
                        )
                    ),
                    "title": attachment.get(
                        "title"
                    ),
                    "creation": attachment.get(
                        "creation"
                    ),
                    "hasUrl": bool(
                        attachment.get("url")
                    ),
                    "hasInlineData": bool(
                        attachment.get("data")
                    ),
                }
            )

        output.append(
            {
                "id": resource.get("id"),
                "status": resource.get(
                    "status"
                ),
                "type": codeable_text(
                    resource.get("type")
                ),
                "category": [
                    codeable_text(item)
                    for item in (
                        resource.get(
                            "category",
                            [],
                        )
                        or []
                    )
                    if codeable_text(item)
                ],
                "date": resource.get("date"),
                "description": resource.get(
                    "description"
                ),
                "content": content,
            }
        )

    return output[:30]

def empty_observation_bundle() -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "entry": [],
    }

async def safely_fetch_targeted_labs(
    patient_id: str,
    targeted_lab_codes: dict[
        str,
        list[str],
    ],
    *,
    access_token: str | None,
    fhir_base_url: str | None,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            fetch_oracle_observations_by_codes(
                patient_id,
                targeted_lab_codes,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=100,
            ),
            timeout=30,
        )

    except asyncio.TimeoutError:
        print(
            "[KGEN TARGETED LAB SEARCH TIMEOUT]",
            {
                "patientId": patient_id,
                "fields": list(
                    targeted_lab_codes
                ),
            },
        )

        return empty_observation_bundle()

    except Exception as error:
        print(
            "[KGEN TARGETED LAB SEARCH ERROR]",
            type(error).__name__,
            str(error),
        )

        return empty_observation_bundle()

clinical_context_service = (
    ClinicalContextService()
)