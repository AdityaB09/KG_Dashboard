from __future__ import annotations

import copy
import math
from typing import Any, Iterable, Mapping


CONSISTENCY_SCHEMA_VERSION = (
    "evidence-consistency-review-v1"
)

SCENARIO_DIAGNOSIS_MAP: dict[
    str,
    dict[str, str],
] = {
    "VFIB-STEMI-001": {
        "code": "VENTRICULAR_FIBRILLATION",
        "display": "Ventricular fibrillation",
    },
    "TORSADES-LQT-002": {
        "code": "TORSADES_DE_POINTES",
        "display": "Torsades de pointes",
    },
    "VT-ISCHEMIC-003": {
        "code": "MONOMORPHIC_VENTRICULAR_TACHYCARDIA",
        "display": "Monomorphic ventricular tachycardia",
    },
    "AFIB-RVR-SEPSIS-004": {
        "code": "ATRIAL_FIBRILLATION_RVR",
        "display": "Atrial fibrillation with rapid ventricular response",
    },
    "CHB-HYPERK-005": {
        "code": "COMPLETE_HEART_BLOCK",
        "display": "Complete heart block",
    },
    "BRADY-DIGTOX-006": {
        "code": "JUNCTIONAL_BRADYCARDIA",
        "display": "Junctional bradycardia",
    },
    "SVT-PSVT-007": {
        "code": "SUPRAVENTRICULAR_TACHYCARDIA",
        "display": "Paroxysmal supraventricular tachycardia",
    },
    "NSVT-ECTOPY-008": {
        "code": "NONSUSTAINED_VENTRICULAR_TACHYCARDIA",
        "display": "Frequent PVCs with non-sustained VT run",
    },
}

MATERIAL_THRESHOLDS = {
    "ventricularRateBpm": 20.0,
    "qrsDurationMs": 20.0,
}


def _number(
    value: Any,
) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")

    if (
        value is None
        or isinstance(value, bool)
    ):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return (
        number
        if math.isfinite(number)
        else None
    )


def _text(
    value: Any,
) -> str:
    return str(
        value
        or ""
    ).strip()


def _walk_dicts(
    value: Any,
) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value

        for item in value.values():
            yield from _walk_dicts(
                item
            )

    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(
                item
            )


def _find_first_value(
    value: Any,
    aliases: set[str],
) -> Any:
    normalized_aliases = {
        "".join(
            character
            for character
            in alias.lower()
            if character.isalnum()
        )
        for alias in aliases
    }

    for mapping in _walk_dicts(
        value
    ):
        for key, item in mapping.items():
            normalized_key = "".join(
                character
                for character
                in str(key).lower()
                if character.isalnum()
            )

            if normalized_key in (
                normalized_aliases
            ):
                return item

    return None


def _append_unique(
    destination: list[str],
    value: str,
) -> None:
    text = str(value).strip()

    if (
        text
        and text
        not in destination
    ):
        destination.append(text)


def _scenario_id(
    diagnostic_event: Mapping[
        str,
        Any,
    ],
    evidence: Mapping[
        str,
        Any,
    ],
) -> str:
    return _text(
        evidence.get("scenarioId")
        or (
            evidence.get(
                "sourceManifest"
            )
            or {}
        ).get("scenarioId")
        or (
            diagnostic_event.get(
                "source"
            )
            or {}
        ).get("identifier")
    )


def _strip_model_visible_benchmark_keys(
    value: Any,
) -> Any:
    blocked = {
        "benchmarksource",
        "answersource",
        "hiddenanswerkey",
        "scenarioanswerkey",
        "benchmarkownership",
    }

    if isinstance(value, dict):
        output: dict[str, Any] = {}

        for key, item in value.items():
            normalized = "".join(
                character
                for character
                in str(key).lower()
                if character.isalnum()
            )

            if normalized in blocked:
                continue

            output[key] = (
                _strip_model_visible_benchmark_keys(
                    item
                )
            )

        return output

    if isinstance(value, list):
        return [
            _strip_model_visible_benchmark_keys(
                item
            )
            for item in value
        ]

    return value


def _normalize_diagnosis(
    *,
    scenario_id: str,
    diagnostic_event: dict[str, Any],
    evidence: dict[str, Any],
    normalizations: list[str],
) -> None:
    expected = (
        SCENARIO_DIAGNOSIS_MAP.get(
            scenario_id
        )
    )

    if not expected:
        return

    diagnosis_targets: list[
        dict[str, Any]
    ] = []

    event_diagnosis = (
        diagnostic_event.setdefault(
            "diagnosis",
            {},
        )
    )
    diagnosis_targets.append(
        event_diagnosis
    )

    controlled = evidence.get(
        "controlledRhythm"
    )

    if isinstance(
        controlled,
        dict,
    ):
        diagnosis_targets.append(
            controlled.setdefault(
                "diagnosis",
                {},
            )
        )

    authoritative = evidence.get(
        "authoritativeDiagnosis"
    )

    if isinstance(
        authoritative,
        dict,
    ):
        diagnosis_targets.append(
            authoritative
        )

    for target in diagnosis_targets:
        previous_code = _text(
            target.get("code")
        )
        previous_display = _text(
            target.get("display")
        )

        target["code"] = (
            expected["code"]
        )
        target["display"] = (
            expected["display"]
        )
        target["authoritative"] = True

        if (
            previous_code
            and previous_code
            != expected["code"]
        ):
            _append_unique(
                normalizations,
                (
                    "Corrected authoritative "
                    "diagnosis code from "
                    f"{previous_code} to "
                    f"{expected['code']} for "
                    f"{scenario_id}."
                ),
            )

        if (
            previous_display
            and previous_display
            != expected["display"]
            and scenario_id
            == "NSVT-ECTOPY-008"
        ):
            _append_unique(
                normalizations,
                (
                    "Normalized NSVT display "
                    "to the controlled scenario "
                    "definition."
                ),
            )


def _qt_contexts(
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    contexts: list[
        dict[str, Any]
    ] = []

    for path in (
        ("controlledEventContext", "qt"),
        ("episodePackContext", "qt"),
    ):
        current: Any = evidence

        for part in path:
            if not isinstance(
                current,
                dict,
            ):
                current = None
                break

            current = current.get(
                part
            )

        if isinstance(
            current,
            dict,
        ):
            contexts.append(
                current
            )

    for mapping in _walk_dicts(
        evidence
    ):
        if (
            "qtcMs" in mapping
            and (
                "prolonged" in mapping
                or "thresholdMs" in mapping
                or "acquiredLongQtSupported"
                in mapping
            )
            and mapping
            not in contexts
        ):
            contexts.append(mapping)

    return contexts


def _qt_medications(
    evidence: Mapping[
        str,
        Any,
    ],
) -> list[str]:
    raw = _find_first_value(
        evidence,
        {
            "qtProlongingMedications",
        },
    )

    if isinstance(raw, list):
        return [
            _text(item)
            for item in raw
            if _text(item)
        ]

    return []


def _normalize_qt(
    evidence: dict[str, Any],
    normalizations: list[str],
) -> None:
    qtc = _number(
        _find_first_value(
            evidence,
            {"qtcMs"},
        )
    )
    medications = (
        _qt_medications(
            evidence
        )
    )

    contexts = _qt_contexts(
        evidence
    )

    if not contexts:
        event_context = (
            evidence.setdefault(
                "controlledEventContext",
                {},
            )
        )

        if isinstance(
            event_context,
            dict,
        ):
            qt = event_context.setdefault(
                "qt",
                {},
            )

            if isinstance(qt, dict):
                contexts.append(qt)

    for context in contexts:
        if qtc is not None:
            context["qtcMs"] = qtc
            context["thresholdMs"] = (
                _number(
                    context.get(
                        "thresholdMs"
                    )
                )
                or 500
            )
            expected_prolonged = bool(
                qtc
                >= float(
                    context[
                        "thresholdMs"
                    ]
                )
            )

            if (
                context.get(
                    "prolonged"
                )
                is not expected_prolonged
            ):
                _append_unique(
                    normalizations,
                    (
                        "Derived QT prolongation "
                        "from the explicit QTc "
                        f"value ({qtc:g} ms)."
                    ),
                )

            context["prolonged"] = (
                expected_prolonged
            )
            context["evidenceSource"] = (
                context.get(
                    "evidenceSource"
                )
                or (
                    "complete_episode_pack."
                    "ecg.measurements.qtcMs"
                )
            )

        if medications:
            context[
                "qtProlongingMedications"
            ] = medications

        prolonged = context.get(
            "prolonged"
        )

        if prolonged is True:
            acquired = (
                True
                if medications
                else None
            )

            if (
                context.get(
                    "acquiredLongQtSupported"
                )
                is not acquired
            ):
                _append_unique(
                    normalizations,
                    (
                        "Qualified acquired "
                        "long-QT support from QTc "
                        "and medication evidence."
                    ),
                )

            context[
                "acquiredLongQtSupported"
            ] = acquired

        elif prolonged is False:
            context[
                "acquiredLongQtSupported"
            ] = False

        elif (
            "acquiredLongQtSupported"
            in context
        ):
            context[
                "acquiredLongQtSupported"
            ] = None

        context["available"] = bool(
            qtc is not None
            or context.get(
                "prolonged"
            )
            is not None
            or medications
        )


def _find_windowed_analysis(
    evidence: Mapping[
        str,
        Any,
    ],
) -> dict[str, Any] | None:
    for mapping in _walk_dicts(
        evidence
    ):
        if (
            mapping.get(
                "schemaVersion"
            )
            == (
                "phase6-windowed-"
                "analysis-v1"
            )
        ):
            return mapping

        windowed = mapping.get(
            "windowedAnalysis"
        )

        if isinstance(
            windowed,
            dict,
        ) and (
            windowed.get(
                "schemaVersion"
            )
            == (
                "phase6-windowed-"
                "analysis-v1"
            )
        ):
            return windowed

    return None


def _controlled_measurement(
    evidence: Mapping[
        str,
        Any,
    ],
    key: str,
) -> float | None:
    controlled = evidence.get(
        "controlledRhythm"
    ) or {}

    if isinstance(
        controlled,
        dict,
    ):
        value = _number(
            controlled.get(key)
        )

        if value is not None:
            return value

    pack = evidence.get(
        "episodePackContext"
    ) or {}

    if isinstance(pack, dict):
        measurements = (
            pack.get(
                "ecgMeasurements"
            )
            or (
                pack.get("ecg")
                or {}
            ).get(
                "measurements"
            )
            or {}
        )

        if isinstance(
            measurements,
            dict,
        ):
            value = _number(
                measurements.get(key)
            )

            if value is not None:
                return value

    return None


def should_emit_measurement_conflict(
    controlled_value: Any,
    phase6_value: Any,
    *,
    same_window: bool,
    same_metric: bool,
    unit_compatible: bool,
    measurement_valid: bool,
    confidence_grade: str,
    absolute_difference: Any,
    threshold: float,
) -> bool:
    controlled = _number(
        controlled_value
    )
    phase6 = _number(
        phase6_value
    )
    difference = _number(
        absolute_difference
    )

    return bool(
        controlled is not None
        and phase6 is not None
        and same_window
        and same_metric
        and unit_compatible
        and measurement_valid
        and str(
            confidence_grade
            or ""
        ).lower()
        in {
            "moderate",
            "high",
        }
        and difference is not None
        and difference
        >= float(threshold)
    )


def _qualified_measurement_conflicts(
    evidence: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    windowed = (
        _find_windowed_analysis(
            evidence
        )
    )

    if not windowed:
        existing = list(
            evidence.get(
                "measurementConflicts"
            )
            or []
        )

        qualified = [
            item
            for item in existing
            if isinstance(item, dict)
            and item.get(
                "sameWindow"
            )
            is True
            and item.get(
                "measurementValid"
            )
            is True
            and str(
                item.get(
                    "confidenceGrade"
                )
                or ""
            ).lower()
            in {
                "moderate",
                "high",
            }
            and item.get(
                "material"
            )
            is True
        ]

        if (
            existing
            and not qualified
        ):
            _append_unique(
                warnings,
                (
                    "Legacy measurement "
                    "differences were suppressed "
                    "because event-window "
                    "comparability, validity, or "
                    "confidence was not established."
                ),
            )

        return qualified

    conflicts: list[
        dict[str, Any]
    ] = []

    definitions = (
        {
            "metric":
                "ventricularRateBpm",
            "controlledKey":
                "ventricularRateBpm",
            "phaseSection":
                "heartRate",
            "phaseKey":
                "eventMedianBpm",
            "validKey":
                "eventMeasurementValid",
            "confidenceKey":
                "eventConfidenceGrade",
            "unit": "bpm",
        },
        {
            "metric":
                "qrsDurationMs",
            "controlledKey":
                "qrsDurationMs",
            "phaseSection": "qrs",
            "phaseKey":
                "eventMedianMs",
            "validKey":
                "eventMeasurementValid",
            "confidenceKey":
                "eventConfidenceGrade",
            "unit": "ms",
        },
    )

    for definition in definitions:
        controlled = (
            _controlled_measurement(
                evidence,
                definition[
                    "controlledKey"
                ],
            )
        )
        phase_section = (
            windowed.get(
                definition[
                    "phaseSection"
                ]
            )
            or {}
        )
        phase_value = _number(
            phase_section.get(
                definition[
                    "phaseKey"
                ]
            )
        )
        valid = bool(
            phase_section.get(
                definition[
                    "validKey"
                ]
            )
        )
        grade = _text(
            phase_section.get(
                definition[
                    "confidenceKey"
                ]
            )
            or (
                windowed.get(
                    "confidence"
                )
                or {}
            ).get("grade")
        ).lower()
        difference = (
            abs(
                controlled
                - phase_value
            )
            if (
                controlled
                is not None
                and phase_value
                is not None
            )
            else None
        )
        threshold = (
            MATERIAL_THRESHOLDS[
                definition["metric"]
            ]
        )

        if should_emit_measurement_conflict(
            controlled,
            phase_value,
            same_window=True,
            same_metric=True,
            unit_compatible=True,
            measurement_valid=valid,
            confidence_grade=grade,
            absolute_difference=difference,
            threshold=threshold,
        ):
            conflicts.append(
                {
                    "id": (
                        "controlled-vs-phase6-"
                        + definition["metric"]
                    ),
                    "metric":
                        definition["metric"],
                    "controlledEventValue":
                        controlled,
                    "phase6EventValue":
                        phase_value,
                    "controlledValue":
                        controlled,
                    "independentValue":
                        phase_value,
                    "unit":
                        definition["unit"],
                    "difference": round(
                        float(
                            difference
                            or 0.0
                        ),
                        3,
                    ),
                    "confidenceGrade":
                        grade,
                    "sameWindow": True,
                    "sameMetric": True,
                    "unitCompatible": True,
                    "measurementValid":
                        valid,
                    "material": True,
                    "requiredAcknowledgement":
                        True,
                }
            )

        elif (
            controlled is not None
            and phase_value
            is not None
            and (
                not valid
                or grade
                in {
                    "",
                    "low",
                    "insufficient",
                }
            )
        ):
            _append_unique(
                warnings,
                (
                    "Phase 6 produced an "
                    f"{grade or 'low'}-confidence "
                    f"controlled-event "
                    f"{definition['metric']} "
                    "measurement. It must not "
                    "dispute or replace the "
                    "controlled episode-package "
                    "value."
                ),
            )

    return conflicts


def _window_conflicts(
    evidence: Mapping[
        str,
        Any,
    ],
) -> list[str]:
    windowed = (
        _find_windowed_analysis(
            evidence
        )
    )

    if not windowed:
        return []

    windows = windowed.get(
        "measurementWindows"
    ) or {}
    full = windows.get(
        "fullCapture"
    ) or {}
    event = windows.get(
        "controlledEvent"
    ) or {}

    full_start = _number(
        full.get("startSeconds")
    )
    full_end = _number(
        full.get("endSeconds")
    )
    event_start = _number(
        event.get("startSeconds")
    )
    event_end = _number(
        event.get("endSeconds")
    )

    if None in {
        full_start,
        full_end,
        event_start,
        event_end,
    }:
        return [
            (
                "Windowed Phase 6 analysis "
                "is missing required capture "
                "or controlled-event boundaries."
            )
        ]

    if not (
        full_start
        <= event_start
        < event_end
        <= full_end
    ):
        return [
            (
                "The controlled-event Phase 6 "
                "window falls outside the saved "
                "full-capture window."
            )
        ]

    return []


def _source_segment_conflicts(
    evidence: Mapping[
        str,
        Any,
    ],
) -> list[str]:
    segments = (
        evidence.get(
            "sourceSegments"
        )
        or (
            evidence.get(
                "episode"
            )
            or {}
        ).get(
            "sourceSegments"
        )
    )

    if not segments:
        return []

    controlled = [
        item
        for item in segments
        if isinstance(item, dict)
        and item.get("type")
        == "controlled_event"
    ]

    if not controlled:
        return [
            (
                "Controlled-event source "
                "provenance is missing from "
                "the saved capture."
            )
        ]

    return []


def _clinical_context_conflicts(
    evidence: Mapping[
        str,
        Any,
    ],
) -> list[str]:
    conflicts: list[str] = []

    qtc = _number(
        _find_first_value(
            evidence,
            {"qtcMs"},
        )
    )
    prolonged = _find_first_value(
        evidence,
        {"prolonged"},
    )

    if (
        qtc is not None
        and qtc >= 500
        and prolonged is False
    ):
        conflicts.append(
            (
                f"QTc is {qtc:g} ms but "
                "prolonged=false."
            )
        )

    for mapping in _walk_dicts(
        evidence
    ):
        if (
            mapping.get("pulseless")
            is True
            and mapping.get(
                "pulsePresent"
            )
            is True
        ):
            conflicts.append(
                (
                    "pulseless=true and "
                    "pulsePresent=true are "
                    "simultaneously asserted."
                )
            )

        if (
            mapping.get(
                "sepsisSupported"
            )
            is True
            and mapping.get(
                "infectionSupported"
            )
            is False
        ):
            conflicts.append(
                (
                    "sepsisSupported=true "
                    "conflicts with "
                    "infectionSupported=false."
                )
            )

    return list(
        dict.fromkeys(
            conflicts
        )
    )


def _diagnosis_mapping_conflicts(
    *,
    scenario_id: str,
    diagnostic_event: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[str]:
    expected = SCENARIO_DIAGNOSIS_MAP.get(scenario_id)
    if not expected:
        return []

    observed: list[tuple[str, str]] = []
    event_diagnosis = diagnostic_event.get("diagnosis") or {}
    if isinstance(event_diagnosis, Mapping):
        code = _text(event_diagnosis.get("code"))
        if code:
            observed.append(("diagnosticEvent.diagnosis.code", code))

    controlled = evidence.get("controlledRhythm") or {}
    controlled_diagnosis = (
        controlled.get("diagnosis")
        if isinstance(controlled, Mapping)
        else {}
    ) or {}
    if isinstance(controlled_diagnosis, Mapping):
        code = _text(controlled_diagnosis.get("code"))
        if code:
            observed.append(("controlledRhythm.diagnosis.code", code))

    conflicts = []
    for path, code in observed:
        if code != expected["code"]:
            conflicts.append(
                f"{path}={code} conflicts with scenario {scenario_id} "
                f"expected code {expected['code']}."
            )
    return list(dict.fromkeys(conflicts))


def apply_evidence_consistency_preflight(
    *,
    diagnostic_event: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    event = copy.deepcopy(
        diagnostic_event
    )
    evidence = copy.deepcopy(
        evidence_bundle
    )
    evidence = (
        _strip_model_visible_benchmark_keys(
            evidence
        )
    )

    normalizations: list[str] = []
    warnings: list[str] = []
    hard_conflicts: list[str] = []

    scenario_id = _scenario_id(
        event,
        evidence,
    )

    # Diagnose contradictions before applying safe completion/normalization.
    # Explicitly contradictory evidence is evidence_invalid, not silently
    # rewritten and not blamed on the model.
    diagnosis_conflicts = _diagnosis_mapping_conflicts(
        scenario_id=scenario_id,
        diagnostic_event=event,
        evidence=evidence,
    )
    clinical_conflicts_before = _clinical_context_conflicts(evidence)
    for conflict in diagnosis_conflicts + clinical_conflicts_before:
        _append_unique(hard_conflicts, conflict)

    if not diagnosis_conflicts:
        _normalize_diagnosis(
            scenario_id=scenario_id,
            diagnostic_event=event,
            evidence=evidence,
            normalizations=normalizations,
        )

    if not any("QTc is " in item for item in clinical_conflicts_before):
        _normalize_qt(
            evidence,
            normalizations,
        )

    conflicts = (
        _qualified_measurement_conflicts(
            evidence,
            warnings,
        )
    )
    evidence[
        "measurementConflicts"
    ] = conflicts
    evidence[
        "measurementConflictLimitations"
    ] = list(warnings)

    for conflict in (
        _window_conflicts(
            evidence
        )
        + _source_segment_conflicts(
            evidence
        )
        + _clinical_context_conflicts(
            evidence
        )
    ):
        _append_unique(
            hard_conflicts,
            conflict,
        )

    expected = SCENARIO_DIAGNOSIS_MAP.get(scenario_id)

    if expected and not diagnosis_conflicts:
        actual_code = _text((event.get("diagnosis") or {}).get("code"))
        if actual_code != expected["code"]:
            _append_unique(
                hard_conflicts,
                "Authoritative diagnosis code does not match the configured scenario mapping.",
            )

    status = (
        "evidence_invalid"
        if hard_conflicts
        else (
            "consistent_with_warnings"
            if warnings
            else "consistent"
        )
    )

    review = {
        "schemaVersion":
            CONSISTENCY_SCHEMA_VERSION,
        "status": status,
        "scenarioId":
            scenario_id,
        "hardConflicts":
            hard_conflicts,
        "warnings": warnings,
        "normalizationsApplied":
            normalizations,
        "qualifiedMeasurementConflictCount":
            len(conflicts),
    }

    evidence[
        "evidenceConsistencyReview"
    ] = review
    evidence[
        "evidenceConsistencyStatus"
    ] = status

    contract = evidence.setdefault(
        "validatorContract",
        {},
    )

    if isinstance(contract, dict):
        contract[
            "evidenceConsistencyStatus"
        ] = status

    return event, evidence, review


def evidence_invalid_validation(
    review: Mapping[str, Any],
    *,
    diagnostic_event: Mapping[
        str,
        Any,
    ],
) -> dict[str, Any]:
    diagnosis = (
        diagnostic_event.get(
            "diagnosis"
        )
        or {}
    )
    conflicts = list(
        review.get(
            "hardConflicts"
        )
        or []
    )

    return {
        "status": "evidence_invalid",
        "accepted": False,
        "hardAccepted": False,
        "displayableWithReview": False,
        "retryable": False,
        "reason": (
            conflicts[0]
            if conflicts
            else (
                "The supplied evidence "
                "failed consistency review."
            )
        ),
        "configurationErrors": [],
        "authoritativeDiagnosisCode":
            diagnosis.get("code"),
        "authoritativeDiagnosisDisplay":
            diagnosis.get("display"),
        "errors": conflicts,
        "hardErrors": [],
        "qualityErrors": [],
        "warnings": list(
            review.get("warnings")
            or []
        ),
        "contradictions": [],
        "unsupportedFacts": [],
        "validatorInternalConflicts":
            conflicts,
        "evidenceCoverage": {},
        "missingRequiredCoverage": [],
        "evidenceCoverageCount": 0,
        "evidenceCoverageRequired": 0,
        "correctionEvidence": [],
        "recommendedActionsRequired":
            False,
        "policyVersion":
            "grounded-response-validator-v4.2",
        "groundingStatus":
            "evidence_invalid",
        "evidenceConsistencyReview":
            dict(review),
    }
