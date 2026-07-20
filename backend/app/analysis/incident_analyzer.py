from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from app.analysis.constants import (
    ALGORITHM_VERSION,
    INCIDENT_SCHEMA_VERSION,
)
from app.analysis.episode_analyzer import (
    episode_analyzer,
)
from app.analysis.io import (
    now_iso,
    read_json,
    write_json_atomic,
)
from app.config import settings
from app.incidents import (
    incident_coordinator,
)


class IncidentAnalyzer:
    def analysis_path(
        self,
        incident_id: str,
    ) -> Path:
        return (
            Path(
                settings
                .INCIDENT_STORAGE_PATH
            )
            / "analysis"
            / f"{incident_id}.json"
        )

    def _update_incident(
        self,
        incident: dict[str, Any],
        **values: Any,
    ) -> None:
        updated = dict(incident)
        updated.update(values)

        updated["updatedAt"] = (
            now_iso()
        )

        incident_coordinator.write_json(
            incident_coordinator
            .incident_file(
                updated["id"]
            ),
            updated,
        )

    @staticmethod
    def _trigger_key(
        metadata: dict[str, Any],
        trigger: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            metadata.get("record"),
            metadata.get(
                "loopNumber"
            ),
            trigger.get(
                "absoluteSample"
            ),
            trigger.get("symbol"),
        )

    def analyze(
        self,
        incident_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        incident = (
            incident_coordinator
            .get_incident(
                incident_id
            )
        )

        episodes = (
            incident_coordinator
            .get_incident_episodes(
                incident_id
            )
        )

        self._update_incident(
            incident,
            analysisStatus="analyzing",
        )

        analyses = []

        analysis_by_episode: dict[
            str,
            dict[str, Any],
        ] = {}

        failures = []

        for episode in episodes:
            try:
                analysis = (
                    episode_analyzer
                    .analyze(
                        episode["id"],
                        force=force,
                    )
                )

                analyses.append(
                    analysis
                )

                analysis_by_episode[
                    episode["id"]
                ] = analysis

            except Exception as error:
                failures.append(
                    {
                        "episodeId": (
                            episode.get(
                                "id"
                            )
                        ),
                        "errorType": (
                            type(
                                error
                            ).__name__
                        ),
                        "message": str(
                            error
                        ),
                    }
                )

        fingerprint_payload = {
            "incidentId": incident_id,
            "episodeIds": (
                incident.get(
                    "episodeIds",
                    [],
                )
            ),
            "episodeFingerprints": {
                episode_id: (
                    analysis.get(
                        "inputFingerprint"
                    )
                )
                for (
                    episode_id,
                    analysis,
                ) in sorted(
                    analysis_by_episode
                    .items()
                )
            },
            "algorithmVersion": (
                ALGORITHM_VERSION
            ),
        }

        incident_fingerprint = (
            hashlib.sha256(
                json.dumps(
                    fingerprint_payload,
                    sort_keys=True,
                    separators=(
                        ",",
                        ":",
                    ),
                ).encode("utf-8")
            ).hexdigest()
        )

        saved_path = (
            self.analysis_path(
                incident_id
            )
        )

        if (
            not force
            and saved_path.exists()
        ):
            try:
                saved = read_json(
                    saved_path
                )

                if (
                    saved.get(
                        "inputFingerprint"
                    )
                    == incident_fingerprint
                    and saved.get(
                        "algorithmVersion"
                    )
                    == ALGORITHM_VERSION
                    and saved.get(
                        "status"
                    )
                    in {
                        "ready",
                        "partial",
                    }
                ):
                    return saved

            except Exception:
                pass

        usable = [
            item
            for item in analyses
            if item.get("status")
            in {
                "ready",
                "partial",
            }
        ]

        ready = [
            item
            for item in analyses
            if item.get("status")
            == "ready"
        ]

        quality_scores = [
            float(
                item.get(
                    "signalQuality",
                    {},
                )
                .get(
                    "overall",
                    {},
                )
                .get(
                    "score",
                    0.0,
                )
            )
            for item in usable
        ]

        morphology_scores = [
            float(
                item[
                    "morphology"
                ][
                    "multiLeadMorphologyScore"
                ]
            )
            for item in usable
            if item.get(
                "morphology",
                {},
            ).get(
                "multiLeadMorphologyScore"
            )
            is not None
        ]

        qrs_trigger = [
            float(
                item[
                    "qrsAnalysis"
                ][
                    (
                        "multiLeadMedian"
                        "TriggerQrsDuration"
                        "Milliseconds"
                    )
                ]
            )
            for item in usable
            if item.get(
                "qrsAnalysis",
                {},
            ).get(
                (
                    "multiLeadMedian"
                    "TriggerQrsDuration"
                    "Milliseconds"
                )
            )
            is not None
        ]

        qrs_baseline = [
            float(
                item[
                    "qrsAnalysis"
                ][
                    (
                        "multiLeadMedian"
                        "BaselineQrsDuration"
                        "Milliseconds"
                    )
                ]
            )
            for item in usable
            if item.get(
                "qrsAnalysis",
                {},
            ).get(
                (
                    "multiLeadMedian"
                    "BaselineQrsDuration"
                    "Milliseconds"
                )
            )
            is not None
        ]

        heart_rates = [
            float(
                item[
                    "rrAnalysis"
                ][
                    "medianHeartRateBpm"
                ]
            )
            for item in usable
            if item.get(
                "rrAnalysis",
                {},
            ).get(
                "medianHeartRateBpm"
            )
            is not None
        ]

        agreement_scores = [
            float(
                item.get(
                    "leadAgreement",
                    {},
                ).get(
                    (
                        "overallMultiLead"
                        "AgreementScore"
                    ),
                    0.0,
                )
            )
            for item in usable
        ]

        confidence_scores = [
            float(
                item.get(
                    "confidence",
                    {},
                ).get(
                    "score",
                    0.0,
                )
            )
            for item in usable
        ]

        best_quality = max(
            usable,
            key=lambda item: (
                item.get(
                    "signalQuality",
                    {},
                )
                .get(
                    "overall",
                    {},
                )
                .get(
                    "score",
                    0.0,
                )
            ),
            default=None,
        )

        best_morphology = max(
            usable,
            key=lambda item: (
                item.get(
                    "morphology",
                    {},
                ).get(
                    (
                        "morphology"
                        "Confidence"
                    ),
                    0.0,
                )
            ),
            default=None,
        )

        unique_triggers: dict[
            tuple[Any, ...],
            dict[str, Any],
        ] = {}

        for metadata in episodes:
            for trigger in (
                metadata.get(
                    "triggerAnnotations"
                )
                or []
            ):
                unique_triggers.setdefault(
                    self._trigger_key(
                        metadata,
                        trigger,
                    ),
                    trigger,
                )

        unique_candidate_beats: set[
            tuple[Any, ...]
        ] = set()

        total_detected_by_episode = 0

        for metadata in episodes:
            analysis = (
                analysis_by_episode.get(
                    metadata["id"]
                )
            )

            if not analysis:
                continue

            total_detected_by_episode += int(
                analysis.get(
                    "ectopicBurden",
                    {},
                ).get(
                    "totalDetectedBeats",
                    0,
                )
            )

            capture_start = float(
                metadata.get(
                    "captureStartSeconds"
                )
                or 0.0
            )

            sampling_rate = float(
                analysis.get(
                    "samplingRateHz"
                )
                or metadata.get(
                    "sampleRate"
                )
                or 1.0
            )

            for beat_index in (
                analysis.get(
                    "ectopicBurden",
                    {},
                )
                .get(
                    (
                        "independently"
                        "Measured"
                    ),
                    {},
                )
                .get(
                    (
                        "candidate"
                        "BeatIndices"
                    ),
                    [],
                )
            ):
                peaks = (
                    analysis.get(
                        "rPeakAnalysis",
                        {},
                    ).get(
                        "rPeakSamples"
                    )
                    or []
                )

                if (
                    0
                    <= int(beat_index)
                    < len(peaks)
                ):
                    absolute_in_loop = (
                        int(
                            round(
                                capture_start
                                * sampling_rate
                            )
                        )
                        + int(
                            peaks[
                                int(
                                    beat_index
                                )
                            ]
                        )
                    )

                    unique_candidate_beats.add(
                        (
                            metadata.get(
                                "record"
                            ),
                            metadata.get(
                                "loopNumber"
                            ),
                            absolute_in_loop,
                        )
                    )

        unique_r_peaks: set[
            tuple[Any, ...]
        ] = set()

        for metadata in episodes:
            analysis = (
                analysis_by_episode.get(
                    metadata["id"]
                )
            )

            if not analysis:
                continue

            capture_start = float(
                metadata.get(
                    "captureStartSeconds"
                )
                or 0.0
            )

            sampling_rate = float(
                analysis.get(
                    "samplingRateHz"
                )
                or metadata.get(
                    "sampleRate"
                )
                or 1.0
            )

            for peak in (
                analysis.get(
                    "rPeakAnalysis",
                    {},
                ).get(
                    "rPeakSamples"
                )
                or []
            ):
                absolute_in_loop = (
                    int(
                        round(
                            capture_start
                            * sampling_rate
                        )
                    )
                    + int(peak)
                )

                unique_r_peaks.add(
                    (
                        metadata.get(
                            "record"
                        ),
                        metadata.get(
                            "loopNumber"
                        ),
                        absolute_in_loop,
                    )
                )

        incident_burden = (
            100.0
            * len(
                unique_candidate_beats
            )
            / max(
                len(unique_r_peaks),
                1,
            )
        )

        cross_episode_quality_agreement = (
            max(
                0.0,
                1.0
                - float(
                    np.std(
                        quality_scores
                    )
                )
                / 25.0,
            )
            if quality_scores
            else 0.0
        )

        cross_episode_qrs_agreement = (
            max(
                0.0,
                1.0
                - float(
                    np.std(
                        qrs_trigger
                    )
                )
                / 30.0,
            )
            if qrs_trigger
            else 0.0
        )

        cross_episode_morphology_agreement = (
            max(
                0.0,
                1.0
                - float(
                    np.std(
                        morphology_scores
                    )
                )
                / 0.25,
            )
            if morphology_scores
            else 0.0
        )

        cross_episode_score = (
            100.0
            * mean(
                [
                    cross_episode_quality_agreement,
                    cross_episode_qrs_agreement,
                    cross_episode_morphology_agreement,
                ]
            )
            if usable
            else 0.0
        )

        incident_confidence_score = (
            float(
                np.median(
                    confidence_scores
                )
            )
            if confidence_scores
            else 0.0
        )

        if (
            failures
            or len(usable)
            < len(episodes)
        ):
            incident_confidence_score = (
                min(
                    incident_confidence_score,
                    69.0,
                )
            )

        if (
            episodes
            and len(ready)
            == len(episodes)
        ):
            status = "ready"
        elif usable:
            status = "partial"
        else:
            status = "failed"

        limitations = [
            (
                "The incident is grouped "
                "from overlapping episode "
                "views; trigger and beat "
                "evidence is deduplicated "
                "by record, loop, and "
                "absolute sample."
            ),
            (
                "INCART V annotations "
                "remain dataset reference "
                "evidence and are not an "
                "independent diagnosis."
            ),
            (
                "FHIR context is a "
                "controlled research "
                "pairing and is not verified "
                "as same-patient clinical "
                "data."
            ),
        ]

        if failures:
            limitations.append(
                (
                    f"{len(failures)} "
                    "episode analysis "
                    "request(s) failed."
                )
            )

        if not episodes:
            limitations.append(
                (
                    "The incident contains "
                    "no readable episode "
                    "metadata."
                )
            )

        result = {
            "schemaVersion": (
                INCIDENT_SCHEMA_VERSION
            ),
            "incidentId": incident_id,
            "status": status,
            "inputFingerprint": (
                incident_fingerprint
            ),
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "algorithmVersion": (
                ALGORITHM_VERSION
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
            "bestQualityEpisodeId": (
                best_quality.get(
                    "episodeId"
                )
                if best_quality
                else None
            ),
            "bestMorphologyEpisodeId": (
                best_morphology.get(
                    "episodeId"
                )
                if best_morphology
                else None
            ),
            "episodeCounts": {
                "listed": len(
                    episodes
                ),
                "analyzed": len(
                    analyses
                ),
                "usable": len(usable),
                "ready": len(ready),
                "failed": len(
                    failures
                ),
            },
            "episodeResults": [
                {
                    "episodeId": (
                        item.get(
                            "episodeId"
                        )
                    ),
                    "status": (
                        item.get(
                            "status"
                        )
                    ),
                    "qualityScore": (
                        item.get(
                            "signalQuality",
                            {},
                        )
                        .get(
                            "overall",
                            {},
                        )
                        .get(
                            "score"
                        )
                    ),
                    "morphologyGrade": (
                        item.get(
                            "morphology",
                            {},
                        ).get(
                            (
                                "morphology"
                                "Grade"
                            )
                        )
                    ),
                    "confidence": (
                        item.get(
                            "confidence"
                        )
                    ),
                }
                for item in analyses
            ],
            "signalQuality": {
                "status": (
                    "ready"
                    if quality_scores
                    else "failed"
                ),
                "meanScore": (
                    round(
                        float(
                            np.mean(
                                quality_scores
                            )
                        ),
                        2,
                    )
                    if quality_scores
                    else None
                ),
                "medianScore": (
                    round(
                        float(
                            np.median(
                                quality_scores
                            )
                        ),
                        2,
                    )
                    if quality_scores
                    else None
                ),
                "minimumScore": (
                    round(
                        float(
                            np.min(
                                quality_scores
                            )
                        ),
                        2,
                    )
                    if quality_scores
                    else None
                ),
                "crossEpisodeAgreement": (
                    round(
                        cross_episode_quality_agreement,
                        4,
                    )
                ),
            },
            "rhythm": {
                "medianHeartRateBpm": (
                    round(
                        float(
                            np.median(
                                heart_rates
                            )
                        ),
                        2,
                    )
                    if heart_rates
                    else None
                ),
                "heartRateRangeBpm": (
                    [
                        round(
                            float(
                                np.min(
                                    heart_rates
                                )
                            ),
                            2,
                        ),
                        round(
                            float(
                                np.max(
                                    heart_rates
                                )
                            ),
                            2,
                        ),
                    ]
                    if heart_rates
                    else None
                ),
            },
            "qrs": {
                "medianTriggerQrsDurationMilliseconds": (
                    round(
                        float(
                            np.median(
                                qrs_trigger
                            )
                        ),
                        3,
                    )
                    if qrs_trigger
                    else None
                ),
                "medianBaselineQrsDurationMilliseconds": (
                    round(
                        float(
                            np.median(
                                qrs_baseline
                            )
                        ),
                        3,
                    )
                    if qrs_baseline
                    else None
                ),
                "crossEpisodeAgreement": (
                    round(
                        cross_episode_qrs_agreement,
                        4,
                    )
                ),
            },
            "morphology": {
                "status": (
                    "ready"
                    if morphology_scores
                    else "failed"
                ),
                "medianDifferenceScore": (
                    round(
                        float(
                            np.median(
                                morphology_scores
                            )
                        ),
                        4,
                    )
                    if morphology_scores
                    else None
                ),
                "crossEpisodeAgreement": (
                    round(
                        cross_episode_morphology_agreement,
                        4,
                    )
                ),
            },
            "leadAgreement": {
                "medianScore": (
                    round(
                        float(
                            np.median(
                                agreement_scores
                            )
                        ),
                        2,
                    )
                    if agreement_scores
                    else None
                ),
            },
            "ectopicBurden": {
                "uniqueTriggerCount": (
                    len(unique_triggers)
                ),
                "referenceVAnnotationCount": (
                    sum(
                        1
                        for key
                        in unique_triggers
                        if key[-1] == "V"
                    )
                ),
                "totalAnalyzedBeatsAcrossEpisodeViews": (
                    total_detected_by_episode
                ),
                "uniqueAnalyzedBeatCount": (
                    len(
                        unique_r_peaks
                    )
                ),
                "uniqueAbnormalMorphologyCandidateCount": (
                    len(
                        unique_candidate_beats
                    )
                ),
                "incidentEctopicBurdenPercent": (
                    round(
                        incident_burden,
                        3,
                    )
                ),
                "overlappingEpisodeViewsDeduplicated": (
                    True
                ),
            },
            "crossEpisodeAgreement": {
                "score": round(
                    cross_episode_score,
                    2,
                ),
                "qualityAgreement": round(
                    cross_episode_quality_agreement,
                    4,
                ),
                "qrsAgreement": round(
                    cross_episode_qrs_agreement,
                    4,
                ),
                "morphologyAgreement": round(
                    cross_episode_morphology_agreement,
                    4,
                ),
            },
            "confidence": {
                "score": round(
                    incident_confidence_score,
                    2,
                ),
                "grade": (
                    "high"
                    if (
                        incident_confidence_score
                        >= 80
                        and not failures
                    )
                    else (
                        "moderate"
                        if incident_confidence_score
                        >= 55
                        else (
                            "low"
                            if incident_confidence_score
                            >= 30
                            else (
                                "insufficient"
                            )
                        )
                    )
                ),
                "episodeConfidenceScores": (
                    confidence_scores
                ),
            },
            "limitations": limitations,
            "errors": failures,
            "provenance": {
                "source": (
                    "deterministic_backend_"
                    "incident_analysis"
                ),
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "datasetAnnotationSource": (
                    "PhysioNet INCART atr"
                ),
                "overlapDeduplicationKey": (
                    "record + loopNumber "
                    "+ absolute sample"
                ),
                "isIndependentDiagnosis": (
                    False
                ),
            },
        }

        write_json_atomic(
            self.analysis_path(
                incident_id
            ),
            result,
        )

        self._update_incident(
            incident,
            analysisStatus=status,
            bestQualityEpisodeId=(
                result[
                    "bestQualityEpisodeId"
                ]
            ),
            bestMorphologyEpisodeId=(
                result[
                    "bestMorphologyEpisodeId"
                ]
            ),
        )

        return result

    def get(
        self,
        incident_id: str,
    ) -> dict[str, Any]:
        incident_coordinator.get_incident(
            incident_id
        )

        path = self.analysis_path(
            incident_id
        )

        if not path.exists():
            return {
                "schemaVersion": (
                    INCIDENT_SCHEMA_VERSION
                ),
                "incidentId": (
                    incident_id
                ),
                "status": (
                    "not_analyzed"
                ),
                "algorithmVersion": (
                    ALGORITHM_VERSION
                ),
                "isIndependentDiagnosis": (
                    False
                ),
            }

        return read_json(path)


incident_analyzer = (
    IncidentAnalyzer()
)