from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


MODEL_FIELDS = (
    "episodeSummary",
    "detectedEpisodeContext",
    "mostLikelyEtiology",
    "contributingFactors",
    "uncertaintyAndMissingData",
)


def _bool(value: Any) -> bool:
    return value is True


def _available_analytes(
    electrolytes: dict[str, Any],
) -> tuple[list[str], list[str]]:
    supplied: list[str] = []
    unavailable: list[str] = []

    for key in ("potassium", "magnesium", "calcium", "sodium"):
        item = electrolytes.get(key) or {}
        if item.get("available") is True:
            supplied.append(key)
        else:
            unavailable.append(key)

    return supplied, unavailable


def _all_supplied_within_reference(
    electrolytes: dict[str, Any],
    supplied: list[str],
) -> bool:
    return bool(supplied) and all(
        (electrolytes.get(key) or {}).get("withinReference") is True
        for key in supplied
    )


def _human_list(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _oracle_is_not_episode_near(
    evidence: dict[str, Any],
) -> bool:
    oracle = evidence.get("oracleContext") or {}
    items = [
        *list(oracle.get("labTrends") or []),
        *list(oracle.get("vitalTrends") or []),
    ]

    if not items:
        return True

    for item in items:
        bucket = str(item.get("temporalBucket") or "").strip().lower()
        relation = str(item.get("relation") or item.get("latestRelation") or "").strip().lower()
        if (
            bucket not in {"episode_near", "near_event", "current"}
            or relation == "after_anchor"
        ):
            return True

    return False


def _replace(
    text: str,
    pattern: str,
    replacement: str,
    *,
    rule_id: str,
    changes: list[dict[str, str]],
    field: str,
) -> str:
    updated, count = re.subn(
        pattern,
        replacement,
        text,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append(
            {
                "field": field,
                "ruleId": rule_id,
                "before": text,
                "after": updated,
            }
        )
    return updated


def _normalize_text(
    text: str,
    *,
    evidence: dict[str, Any],
    field: str,
    changes: list[dict[str, str]],
) -> str:
    event = evidence.get("controlledEventContext") or {}
    electrolytes = event.get("electrolytes") or {}
    infection = event.get("infection") or {}
    renal = event.get("renal") or {}
    metabolic = event.get("metabolic") or {}

    supplied, unavailable = _available_analytes(electrolytes)
    supplied_within = _all_supplied_within_reference(
        electrolytes,
        supplied,
    )

    if supplied_within:
        electrolyte_replacement = (
            f"the supplied {_human_list(supplied)} values were within reference"
        )
        if unavailable:
            electrolyte_replacement += (
                f"; {_human_list(unavailable)} "
                f"{'was' if len(unavailable) == 1 else 'were'} not supplied"
            )

        text = _replace(
            text,
            r"\bno electrolyte abnormalities(?: were)?(?: detected| identified)?\b",
            electrolyte_replacement,
            rule_id="partial_electrolyte_scope",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\babsence of electrolyte abnormalities\b",
            electrolyte_replacement,
            rule_id="partial_electrolyte_scope",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\belectrolytes were (?:normal|within normal limits)\b",
            electrolyte_replacement,
            rule_id="partial_electrolyte_scope",
            changes=changes,
            field=field,
        )

    infection_not_supported = (
        infection.get("infectionSupported") is False
        and infection.get("sepsisSupported") is False
    )
    renal_not_supported = (
        renal.get("renalImpairmentSupported") is False
        and renal.get("chronicKidneyDiseaseSupported") is False
        and renal.get("acuteKidneyInjurySupported") is False
    )

    ph_value = (
        (metabolic.get("ph") or {}).get("value")
    )
    lactate_value = (
        (metabolic.get("lactate") or {}).get("value")
    )

    metabolic_acidosis_present = (
        isinstance(ph_value, (int, float))
        and ph_value < 7.35
    )
    hyperlactatemia_present = (
        isinstance(lactate_value, (int, float))
        and lactate_value > 2.2
    )

    if infection_not_supported and renal_not_supported:
        text = _replace(
            text,
            r"\b(?:with\s+)?no recent renal impairment or infection markers\b",
            (
                "while controlled-event evidence did not support "
                "renal impairment, infection, or sepsis"
            ),
            rule_id="source_qualify_infection_and_renal",
            changes=changes,
            field=field,
        )

        compound_replacement = (
            "while controlled-event evidence did not support "
            "renal impairment, infection, or sepsis"
        )

        metabolic_findings: list[str] = []
        if metabolic_acidosis_present:
            metabolic_findings.append(
                "metabolic acidosis"
            )
        if hyperlactatemia_present:
            metabolic_findings.append(
                "hyperlactatemia"
            )

        if metabolic_findings:
            compound_replacement += (
                "; "
                + _human_list(metabolic_findings)
                + " "
                + (
                    "was present"
                    if len(metabolic_findings) == 1
                    else "were present"
                )
            )

        text = _replace(
            text,
            (
                r"\b(?:with\s+)?no recent renal impairment,\s*"
                r"infection,\s*or metabolic derangements\b"
            ),
            compound_replacement,
            rule_id=(
                "source_qualify_renal_infection_"
                "preserve_metabolic"
            ),
            changes=changes,
            field=field,
        )

    if infection_not_supported:
        text = _replace(
            text,
            r"\bno evidence of infection or sepsis\b",
            (
                "controlled-event evidence did not support "
                "infection or sepsis"
            ),
            rule_id="source_qualify_infection",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\bno infection or sepsis evidence\b",
            (
                "controlled-event evidence did not support "
                "infection or sepsis"
            ),
            rule_id="source_qualify_infection",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\bno evidence for infection or sepsis\b",
            (
                "controlled-event evidence did not support "
                "infection or sepsis"
            ),
            rule_id="source_qualify_infection",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\bno recent infection markers\b",
            (
                "controlled-event evidence did not support "
                "infection"
            ),
            rule_id="source_qualify_infection",
            changes=changes,
            field=field,
        )

    if renal_not_supported:
        text = _replace(
            text,
            r"\bno recent renal impairment\b",
            "controlled-event evidence did not support renal impairment",
            rule_id="source_qualify_renal",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\bno chronic kidney disease or acute kidney injury(?: was)? documented\b",
            (
                "controlled-event evidence did not support chronic kidney disease "
                "or acute kidney injury; Oracle conditions were not returned"
            ),
            rule_id="source_qualify_renal",
            changes=changes,
            field=field,
        )

    if _oracle_is_not_episode_near(evidence):
        text = _replace(
            text,
            r"\bhistorical Oracle data shows stable vitals and labs prior to (?:the )?event\b",
            (
                "available Oracle observations were historical or temporally remote "
                "and were not treated as episode-time physiology"
            ),
            rule_id="oracle_temporal_scope",
            changes=changes,
            field=field,
        )
        text = _replace(
            text,
            r"\bstable vitals and labs prior to (?:the )?event\b",
            (
                "historical or temporally remote Oracle observations that were not "
                "treated as episode-time physiology"
            ),
            rule_id="oracle_temporal_scope",
            changes=changes,
            field=field,
        )

    text = _replace(
        text,
        r"\bmissing information on recent medication adherence\b",
        (
            "available Oracle medication orders do not establish "
            "administration or adherence"
        ),
        rule_id="medication_order_semantics",
        changes=changes,
        field=field,
    )

    text = _replace(
        text,
        (
            r"\bindependent ECG analysis confirmed ventricular "
            r"fibrillation with no abnormal morphology candidates\b"
        ),
        (
            "Phase 6 measured waveform morphology and found no "
            "independent abnormal-morphology candidates; it did "
            "not independently diagnose or negate the controlled "
            "ventricular-fibrillation label"
        ),
        rule_id="phase6_diagnosis_ownership",
        changes=changes,
        field=field,
    )
    text = _replace(
        text,
        (
            r"\bindependent ECG analysis confirmed ventricular "
            r"fibrillation\b"
        ),
        (
            "Phase 6 supplied independent waveform measurements "
            "but did not independently diagnose ventricular "
            "fibrillation"
        ),
        rule_id="phase6_diagnosis_ownership",
        changes=changes,
        field=field,
    )

    text = re.sub(
        r"\bavailable Oracle observations\b",
        "Available Oracle observations",
        text,
    )
    text = text.replace(
        (
            "physiology, with controlled-event evidence"
        ),
        (
            "physiology; controlled-event evidence"
        ),
    )

    return re.sub(r"\s+", " ", text).strip()


def normalize_reviewable_response(
    response: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """
    Apply deterministic, evidence-aware wording corrections.

    This function never changes the fixed diagnosis, measurements, or etiology.
    It only narrows over-broad negative statements and temporal language that the
    deterministic validator already classifies as reviewable rather than unsafe.
    """
    normalized = deepcopy(response)
    changes: list[dict[str, str]] = []

    for field in MODEL_FIELDS:
        value = normalized.get(field)

        if isinstance(value, str):
            normalized[field] = _normalize_text(
                value,
                evidence=evidence,
                field=field,
                changes=changes,
            )
        elif isinstance(value, list):
            updated: list[Any] = []
            for index, item in enumerate(value):
                if isinstance(item, str):
                    updated.append(
                        _normalize_text(
                            item,
                            evidence=evidence,
                            field=f"{field}[{index}]",
                            changes=changes,
                        )
                    )
                else:
                    updated.append(item)
            normalized[field] = updated

    return normalized, changes
