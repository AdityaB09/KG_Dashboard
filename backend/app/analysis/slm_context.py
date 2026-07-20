from __future__ import annotations

from typing import Any

from app.analysis.incident_analyzer import (
    incident_analyzer,
)
from app.incidents import (
    incident_coordinator,
)


def _compact_episode(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "episodeId": (
            analysis.get(
                "episodeId"
            )
        ),
        "status": (
            analysis.get("status")
        ),
        "signalQuality": {
            "status": (
                analysis.get(
                    "signalQuality",
                    {},
                ).get("status")
            ),
            "overall": (
                analysis.get(
                    "signalQuality",
                    {},
                ).get("overall")
            ),
        },
        "rPeakSummary": {
            key: analysis.get(
                "rPeakAnalysis",
                {},
            ).get(key)
            for key in (
                "status",
                "primaryTimingLead",
                "detectedBeatCount",
                "datasetAnnotationSample",
                "nearestDetectedRPeakSample",
                (
                    "triggerAlignmentError"
                    "Milliseconds"
                ),
                "triggerBeatIndex",
                "rPeakAgreement",
                "confidence",
            )
        },
        "rrSummary": {
            key: analysis.get(
                "rrAnalysis",
                {},
            ).get(key)
            for key in (
                "status",
                "meanRrMilliseconds",
                "medianRrMilliseconds",
                "minimumRrMilliseconds",
                "maximumRrMilliseconds",
                (
                    "rrStandardDeviation"
                    "Milliseconds"
                ),
                (
                    "robustRrVariability"
                    "Milliseconds"
                ),
                "rhythmRegularity",
                (
                    "triggerCoupling"
                    "IntervalMilliseconds"
                ),
                "couplingRatio",
                "prematureTimingEvidence",
                (
                    "postTriggerPause"
                    "Milliseconds"
                ),
                (
                    "compensatoryPause"
                    "Status"
                ),
                "confidence",
            )
        },
        "heartRateSummary": {
            key: analysis.get(
                "rrAnalysis",
                {},
            ).get(key)
            for key in (
                "meanHeartRateBpm",
                "medianHeartRateBpm",
                "minimumHeartRateBpm",
                "maximumHeartRateBpm",
            )
        },
        "qrsSummary": {
            key: analysis.get(
                "qrsAnalysis",
                {},
            ).get(key)
            for key in (
                "status",
                (
                    "multiLeadMedian"
                    "TriggerQrsDuration"
                    "Milliseconds"
                ),
                (
                    "multiLeadMedian"
                    "BaselineQrsDuration"
                    "Milliseconds"
                ),
                (
                    "multiLeadMedian"
                    "WidthDifference"
                    "Milliseconds"
                ),
                (
                    "interLeadDuration"
                    "Agreement"
                ),
                "bestQrsWidthLead",
            )
        },
        "morphologySummary": {
            key: analysis.get(
                "morphology",
                {},
            ).get(key)
            for key in (
                "status",
                (
                    "multiLead"
                    "MorphologyScore"
                ),
                "morphologyGrade",
                "morphologyConfidence",
                "bestMorphologyLead",
                "excludedLeadIds",
            )
        },
        "ventricularEctopyEvidence": (
            analysis.get(
                (
                    "ventricular"
                    "EctopyEvidence"
                )
            )
        ),
        "leadAgreement": (
            analysis.get(
                "leadAgreement"
            )
        ),
        "ectopicBurden": (
            analysis.get(
                "ectopicBurden"
            )
        ),
        "confidence": (
            analysis.get(
                "confidence"
            )
        ),
        "limitations": (
            analysis.get(
                "limitations"
            )
        ),
        "provenance": (
            analysis.get(
                "provenance"
            )
        ),
    }


def build_phase6_slm_context(
    incident_id: str,
) -> dict[str, Any]:
    base = (
        incident_coordinator
        .build_slm_context(
            incident_id
        )
    )

    incident_analysis = (
        incident_analyzer.get(
            incident_id
        )
    )

    episode_analyses = []

    for episode in (
        incident_coordinator
        .get_incident_episodes(
            incident_id
        )
    ):
        try:
            from app.analysis.episode_analyzer import (
                episode_analyzer,
            )

            analysis = (
                episode_analyzer.get(
                    episode["id"]
                )
            )

        except Exception:
            continue

        if analysis.get("status") in {
            "ready",
            "partial",
        }:
            episode_analyses.append(
                _compact_episode(
                    analysis
                )
            )

    base["analysisStatus"] = (
        incident_analysis.get(
            "status",
            "not_analyzed",
        )
    )

    base["signalQuality"] = {
        "status": (
            "ready"
            if episode_analyses
            else "not_analyzed"
        ),
        "incidentSummary": (
            incident_analysis.get(
                "signalQuality"
            )
        ),
        "episodeResults": [
            {
                **item[
                    "signalQuality"
                ],
                "episodeId": (
                    item["episodeId"]
                ),
            }
            for item
            in episode_analyses
        ],
    }

    morphology_ready = any(
        item[
            "morphologySummary"
        ].get("status")
        == "ready"
        for item in episode_analyses
    )

    base["morphology"] = {
        "status": (
            "ready"
            if morphology_ready
            else (
                "partial"
                if episode_analyses
                else "not_analyzed"
            )
        ),
        "incidentSummary": (
            incident_analysis.get(
                "morphology"
            )
        ),
        "episodeResults": [
            {
                **item[
                    "morphologySummary"
                ],
                "episodeId": (
                    item["episodeId"]
                ),
            }
            for item
            in episode_analyses
        ],
    }

    base[
        "deterministicEcgEvidence"
    ] = {
        "episodeAnalyses": (
            episode_analyses
        ),
        "incidentAnalysis": {
            key: incident_analysis.get(
                key
            )
            for key in (
                "status",
                "signalQuality",
                "rhythm",
                "qrs",
                "morphology",
                "leadAgreement",
                "ectopicBurden",
                (
                    "crossEpisode"
                    "Agreement"
                ),
                "confidence",
                "limitations",
                "provenance",
            )
        },
    }

    base.setdefault(
        "limitations",
        [],
    )

    required_limitation = (
        "The deterministic ECG "
        "analysis supports evidence "
        "interpretation only and is "
        "not an independent diagnosis."
    )

    if (
        required_limitation
        not in base["limitations"]
    ):
        base[
            "limitations"
        ].append(
            required_limitation
        )

    base.setdefault(
        "provenance",
        {},
    )[
        "deterministicAnalysis"
    ] = {
        "source": (
            "deterministic_"
            "backend_analysis"
        ),
        "algorithmVersion": (
            incident_analysis.get(
                "algorithmVersion"
            )
        ),
        "isIndependentDiagnosis": (
            False
        ),
    }

    return base