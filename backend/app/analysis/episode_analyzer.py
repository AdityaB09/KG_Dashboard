from __future__ import annotations

import os
from typing import Any, Mapping

import numpy as np

from app.analysis.beat_segmentation import build_reference_templates, segment_beats
from app.analysis.confidence import (
    apply_physiology_confidence_penalties,
    calculate_confidence,
)
from app.analysis.constants import ALGORITHM_VERSION, EPISODE_SCHEMA_VERSION
from app.analysis.ectopy import analyze_episode_ectopy
from app.analysis.io import (
    load_episode_input,
    now_iso,
    read_json,
    reusable_analysis,
    write_json_atomic,
)
from app.analysis.lead_agreement import analyze_lead_agreement
from app.analysis.models import AnalysisInputError
from app.analysis.morphology import analyze_morphology
from app.analysis.preprocessing import preprocess_leads
from app.analysis.qrs import analyze_qrs, calibrate_qrs_confidence
from app.analysis.r_peaks import analyze_r_peaks
from app.analysis.rr_metrics import analyze_rr_metrics
from app.analysis.signal_quality import analyze_signal_quality
from app.analysis.windowed_analysis import (
    WINDOWED_SCHEMA_VERSION,
    build_windowed_phase6_analysis,
)


HEART_RATE_AGREEMENT_WARNING_FRACTION = 0.35
ANNOTATION_MIN_VALID_RR_MS = 250.0
ANNOTATION_MAX_VALID_RR_MS = 2500.0


def _feature_enabled(
    name: str,
    default: bool = True,
) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if not np.isfinite(numeric):
        return None

    return int(round(numeric))


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if not np.isfinite(numeric):
        return None

    return numeric


def _round_or_none(
    value: float | int | None,
    digits: int = 4,
) -> float | None:
    numeric = _safe_float(value)
    return None if numeric is None else round(numeric, digits)


def _append_unique(
    destination: list[str],
    values: list[str] | tuple[str, ...],
) -> None:
    for value in values:
        text = str(value).strip()

        if text and text not in destination:
            destination.append(text)


def _first_trigger_annotation_sample(
    metadata: Mapping[str, Any],
) -> int | None:
    for annotation in metadata.get("triggerAnnotations") or []:
        if not isinstance(annotation, Mapping):
            continue

        sample = _safe_int(
            annotation.get("captureOffsetSamples")
        )

        if sample is not None:
            return sample

    return None


def _annotation_timing_summary(
    metadata: Mapping[str, Any],
    sampling_rate_hz: float,
) -> dict[str, Any]:
    beat_samples: list[int] = []
    symbols: list[str] = []

    for annotation in metadata.get("annotations") or []:
        if not isinstance(annotation, Mapping):
            continue

        category = str(
            annotation.get("category") or ""
        ).strip().lower()

        mode = str(
            annotation.get("mode") or ""
        ).strip().lower()

        if category == "signal_quality":
            continue

        if mode and mode not in {"beat", "context"}:
            continue

        sample = _safe_int(
            annotation.get("captureOffsetSamples")
        )

        if sample is None:
            continue

        beat_samples.append(sample)

        symbol = str(
            annotation.get("symbol") or ""
        ).strip()

        if symbol:
            symbols.append(symbol)

    unique_samples = sorted(set(beat_samples))

    result: dict[str, Any] = {
        "source": "PhysioNet INCART atr annotation timing",
        "annotationBeatCount": len(unique_samples),
        "annotationSymbols": sorted(set(symbols)),
        "minimumSupportedRrMilliseconds": ANNOTATION_MIN_VALID_RR_MS,
        "maximumSupportedRrMilliseconds": ANNOTATION_MAX_VALID_RR_MS,
        "usedForPeakDetection": False,
        "usedForValidationOnly": True,
    }

    if len(unique_samples) < 3:
        return {
            **result,
            "status": "unavailable",
            "failureReason": "fewer_than_three_timed_beat_annotations",
            "validIntervalCount": 0,
            "excludedIntervalCount": 0,
            "excludedIntervalPercent": 0.0,
            "medianRrMilliseconds": None,
            "medianHeartRateBpm": None,
            "minimumHeartRateBpm": None,
            "maximumHeartRateBpm": None,
        }

    intervals_samples = np.diff(
        np.asarray(
            unique_samples,
            dtype=np.int64,
        )
    )

    intervals_ms = (
        intervals_samples
        / float(sampling_rate_hz)
        * 1000.0
    )

    valid_mask = (
        intervals_ms
        >= ANNOTATION_MIN_VALID_RR_MS
    ) & (
        intervals_ms
        <= ANNOTATION_MAX_VALID_RR_MS
    )

    valid_intervals_ms = intervals_ms[
        valid_mask
    ]

    excluded_count = int(
        intervals_ms.size
        - valid_intervals_ms.size
    )

    excluded_percent = (
        100.0
        * excluded_count
        / max(1, intervals_ms.size)
    )

    if valid_intervals_ms.size < 2:
        return {
            **result,
            "status": "unavailable",
            "failureReason": "fewer_than_two_valid_annotation_intervals",
            "validIntervalCount": int(
                valid_intervals_ms.size
            ),
            "excludedIntervalCount": excluded_count,
            "excludedIntervalPercent": round(
                excluded_percent,
                3,
            ),
            "medianRrMilliseconds": None,
            "medianHeartRateBpm": None,
            "minimumHeartRateBpm": None,
            "maximumHeartRateBpm": None,
        }

    heart_rates = (
        60000.0 / valid_intervals_ms
    )

    return {
        **result,
        "status": (
            "ready"
            if excluded_percent <= 10.0
            else "partial"
        ),
        "failureReason": None,
        "validIntervalCount": int(
            valid_intervals_ms.size
        ),
        "excludedIntervalCount": excluded_count,
        "excludedIntervalPercent": round(
            excluded_percent,
            3,
        ),
        "medianRrMilliseconds": round(
            float(
                np.median(
                    valid_intervals_ms
                )
            ),
            3,
        ),
        "medianHeartRateBpm": round(
            float(
                np.median(
                    heart_rates
                )
            ),
            3,
        ),
        "minimumHeartRateBpm": round(
            float(
                np.min(
                    heart_rates
                )
            ),
            3,
        ),
        "maximumHeartRateBpm": round(
            float(
                np.max(
                    heart_rates
                )
            ),
            3,
        ),
    }


def _difference_fraction(
    calculated: float | None,
    reference: float | None,
) -> float | None:
    if (
        calculated is None
        or reference is None
        or reference <= 0.0
    ):
        return None

    return abs(
        calculated - reference
    ) / max(
        reference,
        1.0,
    )


def _attach_heart_rate_validation(
    r_peak_analysis: dict[str, Any],
    rr_analysis: dict[str, Any],
    annotation_timing: Mapping[str, Any],
    metadata_heart_rate_bpm: float | None,
) -> None:
    calculated_hr = _safe_float(
        rr_analysis.get(
            "medianHeartRateBpm"
        )
    )

    annotation_hr = _safe_float(
        annotation_timing.get(
            "medianHeartRateBpm"
        )
    )

    metadata_hr = _safe_float(
        metadata_heart_rate_bpm
    )

    annotation_difference = (
        _difference_fraction(
            calculated_hr,
            annotation_hr,
        )
    )

    metadata_difference = (
        _difference_fraction(
            calculated_hr,
            metadata_hr,
        )
    )

    reasons: list[str] = []

    if calculated_hr is None:
        reasons.append(
            "calculated_median_heart_rate_unavailable"
        )

    if (
        annotation_difference is not None
        and annotation_difference
        > HEART_RATE_AGREEMENT_WARNING_FRACTION
    ):
        reasons.append(
            "calculated_hr_differs_materially_from_annotation_timing"
        )

    if (
        metadata_difference is not None
        and metadata_difference
        > HEART_RATE_AGREEMENT_WARNING_FRACTION
    ):
        reasons.append(
            "calculated_hr_differs_materially_from_metadata_hr"
        )

    comparison_status = (
        "ready"
        if not reasons
        else "partial"
    )

    rr_analysis["annotationTiming"] = dict(
        annotation_timing
    )

    rr_analysis["heartRateValidation"] = {
        "status": comparison_status,
        "agreementWarningFraction": (
            HEART_RATE_AGREEMENT_WARNING_FRACTION
        ),
        "calculatedMedianHeartRateBpm": (
            _round_or_none(
                calculated_hr,
                3,
            )
        ),
        "annotationTimingHeartRateBpm": (
            _round_or_none(
                annotation_hr,
                3,
            )
        ),
        "metadataHeartRateBpm": (
            _round_or_none(
                metadata_hr,
                3,
            )
        ),
        "calculatedVsAnnotationDifferenceBpm": (
            _round_or_none(
                (
                    abs(
                        calculated_hr
                        - annotation_hr
                    )
                    if (
                        calculated_hr is not None
                        and annotation_hr is not None
                    )
                    else None
                ),
                3,
            )
        ),
        "calculatedVsAnnotationDifferenceFraction": (
            _round_or_none(
                annotation_difference,
                4,
            )
        ),
        "calculatedVsMetadataDifferenceBpm": (
            _round_or_none(
                (
                    abs(
                        calculated_hr
                        - metadata_hr
                    )
                    if (
                        calculated_hr is not None
                        and metadata_hr is not None
                    )
                    else None
                ),
                3,
            )
        ),
        "calculatedVsMetadataDifferenceFraction": (
            _round_or_none(
                metadata_difference,
                4,
            )
        ),
        "annotationTimingUsedForDetection": False,
        "metadataUsedForDetection": False,
        "reasons": reasons,
    }

    validation = dict(
        r_peak_analysis.get(
            "validation"
        )
        or {}
    )

    validation_reasons = list(
        validation.get("reasons")
        or []
    )

    if annotation_hr is not None:
        validation[
            "annotationTimingHeartRateBpm"
        ] = _round_or_none(
            annotation_hr,
            3,
        )

        validation[
            "annotationHeartRateDifferenceFraction"
        ] = _round_or_none(
            annotation_difference,
            4,
        )

        validation[
            "annotationTimingUsedForDetection"
        ] = False

    if metadata_hr is not None:
        validation[
            "metadataHeartRateBpm"
        ] = _round_or_none(
            metadata_hr,
            3,
        )

        validation[
            "metadataHeartRateDifferenceFraction"
        ] = _round_or_none(
            metadata_difference,
            4,
        )

    _append_unique(
        validation_reasons,
        reasons,
    )

    validation["reasons"] = (
        validation_reasons
    )

    if validation_reasons:
        validation["status"] = "partial"

        if (
            r_peak_analysis.get("status")
            == "ready"
        ):
            r_peak_analysis["status"] = (
                "partial"
            )

        r_peak_analysis["confidence"] = min(
            float(
                r_peak_analysis.get(
                    "confidence"
                )
                or 0.0
            ),
            75.0,
        )

    r_peak_analysis["validation"] = (
        validation
    )

    if not reasons:
        return

    if (
        rr_analysis.get("status")
        != "failed"
    ):
        rr_analysis["status"] = "partial"

    limitations = list(
        rr_analysis.get(
            "limitations"
        )
        or []
    )

    if (
        "calculated_hr_differs_materially_from_annotation_timing"
        in reasons
    ):
        limitations.append(
            "Calculated median heart rate differs materially "
            "from the INCART annotation-timing estimate. "
            "Annotation timing was used only for validation."
        )

    if (
        "calculated_hr_differs_materially_from_metadata_hr"
        in reasons
    ):
        limitations.append(
            "Calculated median heart rate differs materially "
            "from triggerHeartRate metadata. Metadata was used "
            "only for validation."
        )

    rr_analysis["limitations"] = list(
        dict.fromkeys(
            limitations
        )
    )


def _section_status(
    value: Mapping[str, Any],
) -> str:
    return str(
        value.get("status")
        or "failed"
    ).lower()


class EpisodeAnalyzer:
    def _save_status(
        self,
        episode_input: Any,
        status: str,
    ) -> None:
        metadata = dict(
            episode_input.metadata
        )

        metadata["analysisStatus"] = status
        metadata[
            "analysisSchemaVersion"
        ] = EPISODE_SCHEMA_VERSION
        metadata[
            "analysisAlgorithmVersion"
        ] = ALGORITHM_VERSION

        if _feature_enabled(
            "PHASE6_WINDOWED_ANALYSIS_ENABLED",
            True,
        ):
            metadata[
                "phase6WindowedSchemaVersion"
            ] = WINDOWED_SCHEMA_VERSION

        metadata["updatedAt"] = now_iso()

        write_json_atomic(
            episode_input.metadata_path,
            metadata,
        )

    def analyze(
        self,
        episode_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        episode_input = (
            load_episode_input(
                episode_id
            )
        )

        if not force:
            cached = reusable_analysis(
                episode_input.analysis_path,
                episode_input.fingerprint,
            )

            if (
                cached is not None
                and cached.get(
                    "algorithmVersion"
                )
                == ALGORITHM_VERSION
                and (
                    not _feature_enabled(
                        "PHASE6_WINDOWED_ANALYSIS_ENABLED",
                        True,
                    )
                    or (
                        cached.get(
                            "windowedAnalysis"
                        )
                        or {}
                    ).get(
                        "schemaVersion"
                    )
                    == WINDOWED_SCHEMA_VERSION
                )
            ):
                return cached

        created_at = now_iso()

        if (
            episode_input
            .analysis_path
            .exists()
        ):
            try:
                previous = read_json(
                    episode_input
                    .analysis_path
                )

                created_at = (
                    previous.get(
                        "createdAt"
                    )
                    or created_at
                )

            except Exception:
                pass

        self._save_status(
            episode_input,
            "analyzing",
        )

        try:
            quality = (
                analyze_signal_quality(
                    episode_input
                    .waveforms_mv,
                    episode_input
                    .sampling_rate_hz,
                )
            )

            usable_leads = list(
                quality.get(
                    "overall",
                    {},
                ).get(
                    "usableLeadIds",
                    [],
                )
            )

            (
                filtered,
                preprocessing,
            ) = preprocess_leads(
                episode_input
                .waveforms_mv,
                episode_input
                .sampling_rate_hz,
                usable_leads,
            )

            metadata = (
                episode_input.metadata
            )

            dataset_annotation_sample = (
                _first_trigger_annotation_sample(
                    metadata
                )
            )

            metadata_heart_rate_bpm = (
                _safe_float(
                    metadata.get(
                        "triggerHeartRate"
                    )
                )
            )

            annotation_timing = (
                _annotation_timing_summary(
                    metadata,
                    episode_input
                    .sampling_rate_hz,
                )
            )

            r_peaks = analyze_r_peaks(
                filtered,
                quality,
                episode_input
                .sampling_rate_hz,
                dataset_annotation_sample=(
                    dataset_annotation_sample
                ),
                metadata_heart_rate_bpm=(
                    metadata_heart_rate_bpm
                ),
            )

            rr = analyze_rr_metrics(
                r_peaks,
                episode_input
                .sampling_rate_hz,
                trigger_beat_index=(
                    r_peaks.get(
                        "triggerBeatIndex"
                    )
                ),
            )

            _attach_heart_rate_validation(
                r_peaks,
                rr,
                annotation_timing,
                metadata_heart_rate_bpm,
            )

            (
                segmentation,
                _,
                beat_arrays,
            ) = segment_beats(
                episode_input
                .waveforms_mv,
                filtered,
                r_peaks,
                metadata,
                episode_input
                .sampling_rate_hz,
            )

            reference_templates = (
                build_reference_templates(
                    beat_arrays,
                    segmentation.get(
                        "selectedReferenceBeatIndices"
                    )
                    or [],
                )
            )

            trigger_index = (
                r_peaks.get(
                    "triggerBeatIndex"
                )
            )

            qrs = analyze_qrs(
                beat_arrays,
                reference_templates,
                trigger_index,
                episode_input
                .sampling_rate_hz,
            )

            qrs = (
                calibrate_qrs_confidence(
                    qrs
                )
            )

            morphology = (
                analyze_morphology(
                    beat_arrays,
                    reference_templates,
                    trigger_index,
                    episode_input
                    .sampling_rate_hz,
                )
            )

            (
                ventricular_evidence,
                ectopic_burden,
            ) = analyze_episode_ectopy(
                metadata,
                rr,
                qrs,
                morphology,
                beat_arrays,
                reference_templates,
                int(
                    r_peaks.get(
                        "detectedBeatCount",
                        0,
                    )
                ),
                trigger_index,
                episode_input
                .sampling_rate_hz,
            )

            ventricular_evidence[
                "datasetAnnotationSample"
            ] = r_peaks.get(
                "datasetAnnotationSample"
            )

            ventricular_evidence[
                "annotationTimingHeartRateBpm"
            ] = annotation_timing.get(
                "medianHeartRateBpm"
            )

            ventricular_evidence[
                "metadataHeartRateBpm"
            ] = metadata_heart_rate_bpm

            lead_agreement = (
                analyze_lead_agreement(
                    quality,
                    r_peaks,
                    qrs,
                    morphology,
                )
            )

            confidence = (
                calculate_confidence(
                    metadata,
                    quality,
                    r_peaks,
                    rr,
                    segmentation,
                    qrs,
                    morphology,
                )
            )

            confidence = (
                apply_physiology_confidence_penalties(
                    confidence,
                    r_peak_analysis=(
                        r_peaks
                    ),
                    rr_analysis=rr,
                    qrs_analysis=qrs,
                    lead_agreement=(
                        lead_agreement
                    ),
                )
            )

            measured_sections = {
                "preprocessing": preprocessing,
                "rPeakAnalysis": r_peaks,
                "rrAnalysis": rr,
                "beatSegmentation": (
                    segmentation
                ),
                "qrsAnalysis": qrs,
                "morphology": morphology,
                "ventricularEctopyEvidence": (
                    ventricular_evidence
                ),
                "leadAgreement": (
                    lead_agreement
                ),
                "ectopicBurden": (
                    ectopic_burden
                ),
            }

            failed_sections = [
                name
                for name, value
                in measured_sections.items()
                if (
                    _section_status(value)
                    == "failed"
                )
            ]

            partial_sections = [
                name
                for name, value
                in measured_sections.items()
                if (
                    _section_status(value)
                    == "partial"
                )
            ]

            essential_missing = bool(
                confidence.get(
                    "essentialMeasurementsMissing"
                )
            )

            status = (
                "ready"
                if (
                    not failed_sections
                    and not partial_sections
                    and not essential_missing
                    and confidence.get(
                        "grade"
                    )
                    != "insufficient"
                )
                else "partial"
            )

            limitations: list[str] = []

            for section in (
                quality,
                r_peaks,
                rr,
                segmentation,
                qrs,
                morphology,
                ventricular_evidence,
                lead_agreement,
                ectopic_burden,
                confidence,
            ):
                _append_unique(
                    limitations,
                    list(
                        section.get(
                            "limitations"
                        )
                        or []
                    ),
                )

            if (
                annotation_timing.get(
                    "status"
                )
                == "unavailable"
            ):
                limitations.append(
                    "Annotation-derived heart rate could not "
                    "be calculated because too few valid timed "
                    "beat annotations were present."
                )

            limitations.extend(
                [
                    (
                        "The INCART V annotation is dataset "
                        "reference evidence, not an independent "
                        "diagnosis."
                    ),
                    (
                        "Calculated ECG heart rate is compared "
                        "with INCART annotation timing and stored "
                        "triggerHeartRate only as validation "
                        "evidence; neither reference is used to "
                        "select R peaks."
                    ),
                    (
                        "Deterministic measurements are research "
                        "outputs and require clinical validation "
                        "before medical-device use."
                    ),
                ]
            )

            limitations = list(
                dict.fromkeys(
                    limitations
                )
            )

            windowed_analysis: dict[
                str,
                Any,
            ] | None = None

            if _feature_enabled(
                "PHASE6_WINDOWED_ANALYSIS_ENABLED",
                True,
            ):
                windowed_analysis = (
                    build_windowed_phase6_analysis(
                        waveforms_mv=(
                            episode_input
                            .waveforms_mv
                        ),
                        metadata=metadata,
                        sample_rate_hz=(
                            episode_input
                            .sampling_rate_hz
                        ),
                        lead_ids=(
                            episode_input
                            .lead_ids
                        ),
                    )
                )

                _append_unique(
                    limitations,
                    list(
                        windowed_analysis.get(
                            "limitations"
                        )
                        or []
                    ),
                )

            result = {
                "schemaVersion": (
                    EPISODE_SCHEMA_VERSION
                ),
                "phase6WindowedSchemaVersion": (
                    WINDOWED_SCHEMA_VERSION
                    if windowed_analysis
                    else None
                ),
                "episodeId": episode_id,
                "status": status,
                "inputFingerprint": (
                    episode_input
                    .fingerprint
                ),
                "createdAt": created_at,
                "updatedAt": now_iso(),
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "samplingRateHz": (
                    episode_input
                    .sampling_rate_hz
                ),
                "leadIds": (
                    episode_input
                    .lead_ids
                ),
                "inputValidation": (
                    episode_input
                    .validation
                ),
                "signalQuality": quality,
                "preprocessing": (
                    preprocessing
                ),
                "rPeakAnalysis": (
                    r_peaks
                ),
                "rrAnalysis": rr,
                "beatSegmentation": (
                    segmentation
                ),
                "qrsAnalysis": qrs,
                "morphology": morphology,
                "ventricularEctopyEvidence": (
                    ventricular_evidence
                ),
                "leadAgreement": (
                    lead_agreement
                ),
                "ectopicBurden": (
                    ectopic_burden
                ),
                "confidence": confidence,
                "windowedAnalysis": (
                    windowed_analysis
                ),
                "measurementWindows": (
                    (
                        windowed_analysis
                        or {}
                    ).get(
                        "measurementWindows"
                    )
                ),
                "windowedHeartRate": (
                    (
                        windowed_analysis
                        or {}
                    ).get(
                        "heartRate"
                    )
                ),
                "windowedQrs": (
                    (
                        windowed_analysis
                        or {}
                    ).get(
                        "qrs"
                    )
                ),
                "windowedMorphology": (
                    (
                        windowed_analysis
                        or {}
                    ).get(
                        "morphology"
                    )
                ),
                "windowedConfidence": (
                    (
                        windowed_analysis
                        or {}
                    ).get(
                        "confidence"
                    )
                ),
                "partialSections": (
                    partial_sections
                ),
                "limitations": limitations,
                "errors": [
                    {
                        "section": section,
                        "type": (
                            "measurement_failure"
                        ),
                    }
                    for section
                    in failed_sections
                ],
                "provenance": {
                    "source": (
                        "deterministic_backend_analysis"
                    ),
                    "algorithmVersion": (
                        ALGORITHM_VERSION
                    ),
                    "datasetAnnotationSource": (
                        "PhysioNet INCART atr"
                    ),
                    "waveformSource": (
                        metadata.get(
                            "provenance",
                            {},
                        ).get(
                            "waveformSource",
                            (
                                "stored episode "
                                "waveforms.npz"
                            ),
                        )
                    ),
                    "heartRateValidation": {
                        "calculatedFrom": (
                            "validated detected "
                            "R-R intervals"
                        ),
                        "comparedWith": [
                            (
                                "PhysioNet INCART "
                                "atr annotation timing"
                            ),
                            (
                                "stored "
                                "triggerHeartRate"
                            ),
                        ],
                        "referencesUsedForDetection": (
                            False
                        ),
                    },
                    "rawWaveformModified": False,
                    "cachedResult": False,
                    "forcedReanalysis": force,
                    "isIndependentDiagnosis": (
                        False
                    ),
                    "windowedMeasurements": (
                        bool(
                            windowed_analysis
                        )
                    ),
                    "measurementWindowSource": (
                        "saved_episode_metadata"
                        if windowed_analysis
                        else None
                    ),
                },
            }

            if windowed_analysis:
                write_json_atomic(
                    episode_input
                    .analysis_path
                    .with_name(
                        "analysis_windowed.json"
                    ),
                    windowed_analysis,
                )

            write_json_atomic(
                episode_input
                .analysis_path,
                result,
            )

            self._save_status(
                episode_input,
                status,
            )

            return result

        except AnalysisInputError:
            self._save_status(
                episode_input,
                "failed",
            )

            raise

        except Exception as error:
            failed = {
                "schemaVersion": (
                    EPISODE_SCHEMA_VERSION
                ),
                "episodeId": episode_id,
                "status": "failed",
                "inputFingerprint": (
                    episode_input
                    .fingerprint
                ),
                "createdAt": created_at,
                "updatedAt": now_iso(),
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "samplingRateHz": (
                    episode_input
                    .sampling_rate_hz
                ),
                "leadIds": (
                    episode_input
                    .lead_ids
                ),
                "inputValidation": (
                    episode_input
                    .validation
                ),
                "signalQuality": {
                    "status": "failed"
                },
                "preprocessing": {
                    "status": "failed"
                },
                "rPeakAnalysis": {
                    "status": "failed"
                },
                "rrAnalysis": {
                    "status": "failed"
                },
                "beatSegmentation": {
                    "status": "failed"
                },
                "qrsAnalysis": {
                    "status": "failed"
                },
                "morphology": {
                    "status": "failed"
                },
                "ventricularEctopyEvidence": {
                    "status": "failed",
                    "isIndependentDiagnosis": (
                        False
                    ),
                },
                "leadAgreement": {
                    "status": "failed"
                },
                "ectopicBurden": {
                    "status": "failed"
                },
                "confidence": {
                    "score": 0.0,
                    "grade": (
                        "insufficient"
                    ),
                    "essentialMeasurementsMissing": (
                        True
                    ),
                },
                "partialSections": [],
                "limitations": [
                    (
                        "Analysis failed before deterministic "
                        "evidence could be completed."
                    )
                ],
                "errors": [
                    {
                        "type": type(
                            error
                        ).__name__,
                        "message": str(
                            error
                        ),
                    }
                ],
                "provenance": {
                    "source": (
                        "deterministic_backend_analysis"
                    ),
                    "algorithmVersion": (
                        ALGORITHM_VERSION
                    ),
                    "datasetAnnotationSource": (
                        "PhysioNet INCART atr"
                    ),
                    "rawWaveformModified": False,
                    "forcedReanalysis": force,
                    "isIndependentDiagnosis": (
                        False
                    ),
                },
            }

            write_json_atomic(
                episode_input
                .analysis_path,
                failed,
            )

            self._save_status(
                episode_input,
                "failed",
            )

            raise

    def get(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        episode_input = (
            load_episode_input(
                episode_id
            )
        )

        if (
            not episode_input
            .analysis_path
            .exists()
        ):
            return {
                "schemaVersion": (
                    EPISODE_SCHEMA_VERSION
                ),
                "episodeId": episode_id,
                "status": "not_analyzed",
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "isIndependentDiagnosis": (
                    False
                ),
            }

        saved = read_json(
            episode_input
            .analysis_path
        )

        if saved.get("status") in {
            "not_started",
            "pending",
        }:
            return {
                "schemaVersion": (
                    EPISODE_SCHEMA_VERSION
                ),
                "episodeId": episode_id,
                "status": "not_analyzed",
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "isIndependentDiagnosis": (
                    False
                ),
            }

        return saved


episode_analyzer = EpisodeAnalyzer()