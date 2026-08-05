from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable


DEFAULT_REFERENCES: dict[str, tuple[float, float]] = {
    "potassium": (3.5, 5.1),
    "magnesium": (1.7, 2.4),
    "calcium": (8.5, 10.5),
    "sodium": (135.0, 145.0),
}

KNOWN_QT_PROLONGING_MEDICATIONS = {
    "sotalol",
    "amiodarone",
    "azithromycin",
    "ondansetron",
    "haloperidol",
    "methadone",
    "levofloxacin",
    "moxifloxacin",
    "dofetilide",
    "quinidine",
    "procainamide",
}

QT_MEDICATION_DISPLAY_NAMES = {
    "sotalol": "Sotalol",
    "amiodarone": "Amiodarone",
    "azithromycin": "Azithromycin",
    "ondansetron": "Ondansetron",
    "haloperidol": "Haloperidol",
    "methadone": "Methadone",
    "levofloxacin": "Levofloxacin",
    "moxifloxacin": "Moxifloxacin",
    "dofetilide": "Dofetilide",
    "quinidine": "Quinidine",
    "procainamide": "Procainamide",
}

SCENARIO_DIAGNOSIS_MAP: dict[str, dict[str, str]] = {
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

DIAGNOSIS_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ventricular fibrillation", "vfib"), "VENTRICULAR_FIBRILLATION"),
    (("torsades", "polymorphic ventricular tachycardia", "polymorphic vt"), "POLYMORPHIC_VT"),
    (("monomorphic ventricular tachycardia", "monomorphic vt"), "MONOMORPHIC_VT"),
    (("atrial fibrillation", "afib"), "ATRIAL_FIBRILLATION_RVR"),
    (("complete heart block", "third degree av block", "complete av block"), "COMPLETE_HEART_BLOCK"),
    (("junctional bradycardia", "symptomatic bradycardia", "bradycardia"), "BRADYCARDIA"),
    (("supraventricular tachycardia", "psvt", "avnrt", "svt"), "SUPRAVENTRICULAR_TACHYCARDIA"),
    (("nonsustained ventricular tachycardia", "non sustained ventricular tachycardia", "nsvt"), "NSVT_ECTOPY"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()

    if normalized in {"true", "yes", "present", "1", "positive"}:
        return True

    if normalized in {"false", "no", "absent", "0", "negative"}:
        return False

    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())

    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)

    return str(value)


NEGATION_PREFIXES = (
    "no ",
    "not ",
    "without ",
    "denies ",
    "absence of ",
    "no evidence of ",
    "negative for ",
)


def _sentences(value: Any) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?;])\s+|\n+", _flatten_text(value))
        if item.strip()
    ]


def _phrase_asserted(value: Any, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(value):
        normalized = sentence.lower()
        for phrase in phrases:
            position = normalized.find(phrase.lower())
            if position < 0:
                continue
            prefix = normalized[max(0, position - 70):position]
            if any(marker in prefix for marker in NEGATION_PREFIXES):
                continue
            return True
    return False


def _phrase_negated(value: Any, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(value):
        normalized = sentence.lower()
        for phrase in phrases:
            position = normalized.find(phrase.lower())
            if position < 0:
                continue
            prefix = normalized[max(0, position - 70):position]
            if any(marker in prefix for marker in NEGATION_PREFIXES):
                return True
    return False


def _tri_phrase(
    value: Any,
    phrases: tuple[str, ...],
) -> bool | None:
    if _phrase_asserted(
        value,
        phrases,
    ):
        return True

    if _phrase_negated(
        value,
        phrases,
    ):
        return False

    return None


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield _normalize_key(key), item
            yield from _walk(item)

    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _find_value(value: Any, aliases: set[str]) -> Any:
    normalized_aliases = {_normalize_key(alias) for alias in aliases}

    for key, item in _walk(value):
        if key in normalized_aliases:
            return item

    return None


def _find_number(value: Any, aliases: set[str]) -> float | None:
    return _number(_find_value(value, aliases))


def _parse_reference(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        low = _number(value.get("low") or value.get("min") or value.get("minimum"))
        high = _number(value.get("high") or value.get("max") or value.get("maximum"))

        if low is not None and high is not None:
            return low, high

        value = value.get("reference") or value.get("referenceRange") or value.get("range")

    if not isinstance(value, str):
        return None

    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(-?\d+(?:\.\d+)?)",
        value,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    low = _number(match.group(1))
    high = _number(match.group(2))

    if low is None or high is None:
        return None

    return low, high


def _entry(mapping: dict[str, Any], aliases: set[str]) -> dict[str, Any] | None:
    normalized_aliases = {_normalize_key(alias) for alias in aliases}

    for key, item in mapping.items():
        if _normalize_key(key) not in normalized_aliases:
            continue

        return item if isinstance(item, dict) else {"value": item}

    return None


def _lab_context(
    labs: dict[str, Any],
    name: str,
    aliases: set[str],
) -> dict[str, Any]:
    entry = _entry(labs, aliases)
    default_reference = DEFAULT_REFERENCES.get(name)

    if not entry:
        return {
            "available": False,
            "value": None,
            "unit": None,
            "withinReference": None,
            "reference": None,
        }

    value = _number(entry.get("value"))
    reference = _parse_reference(
        entry.get("reference")
        or entry.get("referenceRange")
        or entry.get("range")
    ) or default_reference

    within_reference: bool | None = None

    if value is not None and reference is not None:
        within_reference = reference[0] <= value <= reference[1]

    return {
        "available": value is not None or bool(entry),
        "value": value,
        "unit": entry.get("unit"),
        "withinReference": within_reference,
        "reference": (
            {"low": reference[0], "high": reference[1]}
            if reference is not None
            else None
        ),
        "note": entry.get("note") or entry.get("status") or entry.get("flag"),
    }



def _diagnosis_code(
    *values: Any,
) -> str:
    combined = " ".join(
        _text(value)
        for value in values
        if value
    ).lower()

    for scenario_id, definition in (
        SCENARIO_DIAGNOSIS_MAP.items()
    ):
        if scenario_id.lower() in combined:
            return definition["code"]

    for aliases, code in DIAGNOSIS_ALIASES:
        if any(
            alias in combined
            for alias in aliases
        ):
            return code

    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        combined,
    ).strip("_")

    return (
        normalized.upper()
        or "CAPTURED_ECG_EVENT"
    )


def _split_findings(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_split_findings(item))
        return output

    if isinstance(value, dict):
        output: list[str] = []
        for item in value.values():
            output.extend(_split_findings(item))
        return output

    return [
        item.strip()
        for item in re.split(r"[,;|]+", _text(value))
        if item.strip()
    ]


def _canonical_rhythm_features(
    measurements: dict[str, Any],
    episode: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    raw_items: list[str] = []

    for value in (
        measurements.get("morphology"),
        measurements.get("morphologyFindings"),
        measurements.get("preEventNote"),
        episode.get("morphology"),
        clinical_context,
    ):
        raw_items.extend(_split_findings(value))

    combined = " ".join(raw_items).lower()
    findings: list[str] = []

    def add(value: str) -> None:
        if value not in findings:
            findings.append(value)

    if "wide" in combined and "monomorphic" in combined and "qrs" in combined:
        add("Wide monomorphic QRS")
    elif "wide" in combined and "qrs" in combined:
        add("Wide QRS")

    if "polymorphic" in combined:
        add("Polymorphic QRS morphology")

    if "twist" in combined or "twisting" in combined:
        add("Twisting QRS axis")

    if "av dissociation" in combined:
        add("AV dissociation")

    if "capture" in combined:
        add("Capture beats")

    if "fusion" in combined:
        add("Fusion beats")

    if "peaked t" in combined:
        add("Peaked T waves")

    if "qrs widening" in combined or "widening qrs" in combined:
        add("Progressive QRS widening")

    if "narrow" in combined and "qrs" in combined:
        add("Narrow QRS")

    if "multifocal" in combined and "pvc" in combined:
        add("Multifocal PVCs")

    if "frequent" in combined and "pvc" in combined:
        add("Frequent PVCs")

    atrial_rate = _number(measurements.get("atrialRateBpm"))
    ventricular_rate = (
        _number(measurements.get("ventricularRateBpm"))
        or _number(measurements.get("heartRateBpm"))
    )
    p_wave_present = _boolean(measurements.get("pWavePresent"))
    atrial_activity_present = (
        True
        if p_wave_present is True or atrial_rate is not None
        else p_wave_present
    )

    av_association = _text(
        measurements.get("atrioventricularAssociation")
        or measurements.get("avAssociation")
    ).lower() or None

    if av_association is None and "av dissociation" in combined:
        av_association = "dissociated"

    qtc_ms = _number(measurements.get("qtcMs"))
    qtc_evidence_source = (
        "complete_episode_pack.ecg.measurements.qtcMs"
        if qtc_ms is not None
        else None
    )
    if qtc_ms is None:
        qtc_match = re.search(
            r"\bqtc(?:\s*(?:was|of|=|:))?\s*(\d{3,4})\s*ms?\b",
            combined,
            flags=re.IGNORECASE,
        )
        if qtc_match:
            qtc_ms = _number(qtc_match.group(1))
            qtc_evidence_source = (
                "complete_episode_pack.ecg.measurements.preEventNote"
            )

    return {
        "ventricularRateBpm": ventricular_rate,
        "atrialRateBpm": atrial_rate,
        "qrsDurationMs": _number(measurements.get("qrsDurationMs")),
        "qtcMs": qtc_ms,
        "qtcEvidenceSource": qtc_evidence_source,
        "prIntervalMs": _number(
            measurements.get("prIntervalMs")
            or measurements.get("prMs")
        ),
        "regularity": measurements.get("regularity") or episode.get("regularity"),
        "axisDegrees": _number(measurements.get("axisDeg")),
        "pWavePresent": p_wave_present,
        "atrialActivityPresent": atrial_activity_present,
        "atrioventricularAssociation": av_association,
        "findings": findings[:14],
    }



def _hemodynamic_context(
    vitals: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    blood_pressure = (
        vitals.get("bloodPressure")
        if isinstance(vitals, dict)
        else {}
    )
    blood_pressure = (
        blood_pressure
        if isinstance(
            blood_pressure,
            dict,
        )
        else {}
    )

    systolic = (
        _number(
            blood_pressure.get(
                "systolic"
            )
        )
        or _find_number(
            vitals,
            {
                "systolic",
                "systolicBloodPressure",
            },
        )
    )
    diastolic = (
        _number(
            blood_pressure.get(
                "diastolic"
            )
        )
        or _find_number(
            vitals,
            {
                "diastolic",
                "diastolicBloodPressure",
            },
        )
    )
    mean_pressure = (
        _number(
            blood_pressure.get("map")
        )
        or _find_number(
            vitals,
            {
                "map",
                "meanArterialPressure",
            },
        )
    )

    evidence = {
        "vitals": vitals,
        "context": clinical_context,
    }

    conscious = _tri_phrase(
        evidence,
        (
            "conscious",
            "alert",
            "awake",
        ),
    )
    if _phrase_asserted(
        evidence,
        (
            "unconscious",
            "unresponsive",
            "loss of consciousness",
        ),
    ):
        conscious = False

    pulse_present = _tri_phrase(
        evidence,
        (
            "pulse present",
            "maintained a pulse",
            "with a pulse",
            "perfusing rhythm",
        ),
    )
    pulseless = _tri_phrase(
        evidence,
        (
            "pulseless",
            "no pulse",
            "cardiac arrest",
        ),
    )

    if pulse_present is True:
        pulseless = False
    elif pulseless is True:
        pulse_present = False

    vasopressor = _tri_phrase(
        evidence,
        (
            "vasopressor",
            "norepinephrine",
            "epinephrine infusion",
            "pressor support",
        ),
    )
    shock = _tri_phrase(
        evidence,
        (
            "septic shock",
            "cardiogenic shock",
            "shock state",
            "in shock",
        ),
    )

    if vasopressor is True:
        shock = True

    if pulseless is True:
        classification = (
            "cardiac_arrest"
        )
    elif (
        shock is True
        or (
            systolic is not None
            and systolic < 80
        )
        or (
            mean_pressure is not None
            and mean_pressure < 60
        )
    ):
        classification = "unstable"
    elif (
        _phrase_asserted(
            evidence,
            (
                "borderline",
                "hypotension",
                "hypotensive",
            ),
        )
        or (
            systolic is not None
            and systolic < 90
        )
        or (
            mean_pressure is not None
            and mean_pressure < 70
        )
    ):
        classification = "borderline"
    elif (
        systolic is not None
        or mean_pressure is not None
        or pulse_present is True
    ):
        classification = "stable"
    else:
        classification = "unknown"

    available = bool(
        vitals
    ) or any(
        value is not None
        for value in (
            conscious,
            pulseless,
            pulse_present,
            shock,
            vasopressor,
            systolic,
            diastolic,
            mean_pressure,
        )
    )

    return {
        "available": available,
        "classification": (
            classification
            if available
            else None
        ),
        "conscious": conscious,
        "pulseless": pulseless,
        "pulsePresent": pulse_present,
        "shockSupported": shock,
        "vasopressorSupport": (
            vasopressor
        ),
        "systolicBloodPressure": (
            systolic
        ),
        "diastolicBloodPressure": (
            diastolic
        ),
        "map": mean_pressure,
        "oxygenSaturationPct":
            _find_number(
                vitals,
                {
                    "spo2",
                    "spo2Pct",
                    "oxygenSaturation",
                },
            ),
        "temperatureC":
            _find_number(
                vitals,
                {
                    "temperature",
                    "temperatureC",
                },
            ),
        "sourceNote":
            blood_pressure.get("note"),
    }


def _electrolyte_context(labs: dict[str, Any]) -> dict[str, Any]:
    potassium = _lab_context(labs, "potassium", {"potassium", "k", "serumPotassium"})
    magnesium = _lab_context(labs, "magnesium", {"magnesium", "mg", "serumMagnesium"})
    calcium = _lab_context(labs, "calcium", {"calcium", "ca", "serumCalcium"})
    sodium = _lab_context(labs, "sodium", {"sodium", "na", "serumSodium"})

    available = any(item["available"] for item in (potassium, magnesium, calcium, sodium))
    abnormal = [
        name
        for name, item in (
            ("potassium", potassium),
            ("magnesium", magnesium),
            ("calcium", calcium),
            ("sodium", sodium),
        )
        if item["withinReference"] is False
    ]

    return {
        "available": available,
        "potassium": potassium,
        "magnesium": magnesium,
        "calcium": calcium,
        "sodium": sodium,
        "abnormalElectrolytes": abnormal,
        "electrolyteAbnormalityPresent": bool(abnormal) if available else None,
        "majorElectrolyteTriggerSupported": (
            bool(abnormal)
            if available
            else None
        ),
    }



def _renal_context(
    labs: dict[str, Any],
    patient: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    evidence = {
        "patient": patient,
        "context": clinical_context,
        "labs": labs,
    }
    combined = _flatten_text(
        evidence
    ).lower()

    creatinine = _find_number(
        labs,
        {
            "creatinine",
            "serumCreatinine",
        },
    )
    bun = _find_number(
        labs,
        {
            "bun",
            "bloodUreaNitrogen",
        },
    )
    egfr = _find_number(
        labs,
        {
            "egfr",
            "estimatedGfr",
        },
    )

    esrd = _tri_phrase(
        evidence,
        (
            "esrd",
            "end stage renal",
            "end-stage renal",
        ),
    )
    dialysis = _tri_phrase(
        evidence,
        (
            "dialysis",
            "hemodialysis",
            "haemodialysis",
        ),
    )
    missed_dialysis = _tri_phrase(
        evidence,
        (
            "missed dialysis",
            "missed hemodialysis",
            "skipped dialysis",
        ),
    )
    chronic_kidney_disease = (
        _tri_phrase(
            evidence,
            (
                "chronic kidney disease",
                "ckd",
                "chronic renal impairment",
                "chronic renal failure",
            ),
        )
    )
    aki = _tri_phrase(
        evidence,
        (
            "acute kidney injury",
            "acute renal failure",
            "acute-on-chronic kidney injury",
            "worsening renal function",
            "worsening creatinine",
        ),
    )

    available = any(
        value is not None
        for value in (
            creatinine,
            bun,
            egfr,
            esrd,
            dialysis,
            missed_dialysis,
            chronic_kidney_disease,
            aki,
        )
    )

    impairment_values = (
        aki,
        chronic_kidney_disease,
        esrd,
    )
    renal_impairment: bool | None

    if any(
        value is True
        for value in impairment_values
    ):
        renal_impairment = True
    elif (
        available
        and all(
            value is False
            for value in impairment_values
            if value is not None
        )
        and any(
            value is not None
            for value in impairment_values
        )
    ):
        renal_impairment = False
    else:
        renal_impairment = None

    return {
        "available": available,
        "creatinine": creatinine,
        "bun": bun,
        "egfr": egfr,
        "esrd": esrd,
        "dialysis": dialysis,
        "missedDialysis": (
            missed_dialysis
        ),
        "chronicKidneyDiseaseSupported":
            chronic_kidney_disease,
        "acuteKidneyInjurySupported":
            aki,
        "renalImpairmentSupported":
            renal_impairment,
        "sourceTextContainsRenalImpairment": (
            True
            if "renal impairment"
            in combined
            else None
        ),
    }



def _ischemia_context(
    labs: dict[str, Any],
    clinical_context: Any,
    measurements: dict[str, Any],
) -> dict[str, Any]:
    troponin_entry = _entry(
        labs,
        {
            "troponin",
            "troponinT",
            "troponinI",
            "highSensitivityTroponin",
        },
    )
    troponin_value = (
        _number(
            troponin_entry.get(
                "value"
            )
        )
        if troponin_entry
        else None
    )
    evidence = {
        "context": clinical_context,
        "troponin": troponin_entry,
        "measurements": measurements,
    }
    combined = _flatten_text(
        evidence
    ).lower()

    chest_pain = _tri_phrase(
        evidence,
        ("chest pain",),
    )

    st_elevation = _tri_phrase(
        evidence,
        (
            "st elevation",
            "st-elevation",
            "anterior stemi",
            "acute stemi",
        ),
    )
    demand = (
        True
        if (
            "demand" in combined
            and troponin_entry
            is not None
        )
        else None
    )

    if demand is True:
        troponin_pattern = (
            "mild_demand_related"
        )
    elif troponin_value is not None:
        troponin_pattern = (
            "measured_unspecified_pattern"
        )
    else:
        troponin_pattern = (
            "not_available"
        )

    acute_stemi_supported: bool | None

    if (
        st_elevation is True
        and chest_pain is not False
    ):
        acute_stemi_supported = True
    elif (
        st_elevation is False
        or demand is True
        or (
            chest_pain is False
            and st_elevation is not True
        )
    ):
        acute_stemi_supported = False
    else:
        acute_stemi_supported = None

    available = bool(
        troponin_entry
    ) or any(
        value is not None
        for value in (
            chest_pain,
            st_elevation,
            demand,
        )
    )

    return {
        "available": available,
        "acuteChestPain": chest_pain,
        "stElevationSupported":
            st_elevation,
        "troponinValue":
            troponin_value,
        "troponinPattern":
            troponin_pattern,
        "acuteStemiSupported":
            acute_stemi_supported,
    }



def _infection_context(
    labs: dict[str, Any],
    vitals: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    evidence = {
        "labs": labs,
        "vitals": vitals,
        "context": clinical_context,
    }

    temperature = _find_number(
        vitals,
        {
            "temperature",
            "temperatureC",
        },
    )
    wbc = _find_number(
        labs,
        {
            "wbc",
            "whiteBloodCellCount",
            "whiteBloodCells",
        },
    )
    procalcitonin = _find_number(
        labs,
        {"procalcitonin"},
    )
    lactate = _find_number(
        labs,
        {
            "lactate",
            "lacticAcid",
        },
    )

    infection_explicit = (
        _tri_phrase(
            evidence,
            (
                "sepsis",
                "urosepsis",
                "infection",
                "pneumonia",
                "positive culture",
                "bacteremia",
                "pyelonephritis",
            ),
        )
    )
    fever_explicit = _tri_phrase(
        evidence,
        (
            "fever",
            "febrile",
        ),
    )
    fever = (
        True
        if (
            temperature is not None
            and temperature >= 38.0
        )
        else fever_explicit
    )
    inflammatory_explicit = (
        _tri_phrase(
            evidence,
            (
                "elevated procalcitonin",
                "leukocytosis",
                (
                    "inflammatory markers "
                    "elevated"
                ),
            ),
        )
    )
    inflammatory = (
        True
        if (
            (
                wbc is not None
                and wbc >= 12.0
            )
            or (
                procalcitonin is not None
                and procalcitonin > 0.5
            )
        )
        else inflammatory_explicit
    )

    if infection_explicit is not None:
        infection_supported = (
            infection_explicit
        )
    elif (
        fever is True
        and inflammatory is True
    ):
        infection_supported = True
    else:
        infection_supported = None

    sepsis_explicit = _tri_phrase(
        evidence,
        (
            "sepsis",
            "urosepsis",
            "septic shock",
        ),
    )

    if sepsis_explicit is not None:
        sepsis_supported = (
            sepsis_explicit
        )
    elif (
        infection_supported is True
        and lactate is not None
        and lactate >= 2.0
    ):
        sepsis_supported = True
    else:
        sepsis_supported = None

    source = None

    for marker in (
        "urosepsis",
        "urinary",
        "pneumonia",
        "lung",
        "bloodstream",
        "bacteremia",
    ):
        if _phrase_asserted(
            evidence,
            (marker,),
        ):
            source = marker
            break

    available = any(
        value is not None
        for value in (
            temperature,
            wbc,
            procalcitonin,
            lactate,
            infection_explicit,
            fever,
            inflammatory,
            sepsis_supported,
        )
    )

    return {
        "available": available,
        "temperatureC": temperature,
        "wbc": wbc,
        "procalcitonin":
            procalcitonin,
        "lactate": lactate,
        "feverSupported": fever,
        "inflammatoryResponseSupported":
            inflammatory,
        "infectionSupported":
            infection_supported,
        "sepsisSupported":
            sepsis_supported,
        "infectionExplicitlyDenied": (
            infection_explicit is False
            if infection_explicit
            is not None
            else None
        ),
        "suspectedSource": source,
    }



def _medication_context(
    patient: dict[str, Any],
    scenario_payload: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    home = (
        patient.get(
            "homeMedications"
        )
        or []
    )
    active = (
        scenario_payload.get(
            "medications"
        )
        or []
    )
    infusions = (
        patient.get("infusions")
        or scenario_payload.get(
            "infusions"
        )
        or []
    )
    available = bool(
        home
        or active
        or infusions
    )
    combined = _flatten_text(
        {
            "home": home,
            "active": active,
            "infusions": infusions,
            "context": clinical_context,
        }
    ).lower()

    qt_prolonging = [
        QT_MEDICATION_DISPLAY_NAMES[
            medication
        ]
        for medication
        in sorted(
            KNOWN_QT_PROLONGING_MEDICATIONS
        )
        if medication in combined
    ]

    def exposure(
        markers: tuple[str, ...],
    ) -> bool | None:
        if not available:
            return None

        return any(
            marker in combined
            for marker in markers
        )

    return {
        "available": available,
        "homeMedications": home,
        "activeMedications": active,
        "infusions": infusions,
        "qtProlongingMedications":
            qt_prolonging,
        "digoxinExposure": exposure(
            ("digoxin",)
        ),
        "potassiumRaisingMedicationExposure":
            exposure(
                (
                    "spironolactone",
                    "ace inhibitor",
                    "arb",
                    "sacubitril",
                    "potassium supplement",
                )
            ),
        "thiazideExposure": exposure(
            (
                "hydrochlorothiazide",
                "chlorthalidone",
                "thiazide",
            )
        ),
    }



def _qt_context(
    rhythm_features: dict[str, Any],
    medication_context: dict[str, Any],
    clinical_context: Any,
) -> dict[str, Any]:
    qtc = _number(
        rhythm_features.get(
            "qtcMs"
        )
    )
    explicit_prolonged = (
        _tri_phrase(
            clinical_context,
            (
                "prolonged qtc",
                "marked qt prolongation",
                "long qt",
                "long-qt",
                "acquired long-qt",
                "acquired long qt",
            ),
        )
    )

    if qtc is not None:
        prolonged: bool | None = (
            qtc >= 500
        )
        evidence_source = (
            rhythm_features.get("qtcEvidenceSource")
            or "complete_episode_pack.ecg.measurements.qtcMs"
        )
    elif explicit_prolonged is not None:
        prolonged = explicit_prolonged
        evidence_source = (
            "complete_episode_pack."
            "clinicalContext"
        )
    else:
        prolonged = None
        evidence_source = None

    qt_medications = list(
        medication_context.get(
            "qtProlongingMedications"
        )
        or []
    )

    acquired_supported: bool | None

    if prolonged is True:
        acquired_supported = (
            True
            if qt_medications
            else None
        )
    elif prolonged is False:
        acquired_supported = False
    else:
        acquired_supported = None

    available = (
        qtc is not None
        or prolonged is not None
        or bool(qt_medications)
    )

    return {
        "available": available,
        "qtcMs": qtc,
        "prolonged": prolonged,
        "thresholdMs": 500,
        "qtProlongingMedications":
            qt_medications,
        "acquiredLongQtSupported":
            acquired_supported,
        "evidenceSource":
            evidence_source,
    }


def _toxicity_context(labs: dict[str, Any], medication_context: dict[str, Any], renal_context: dict[str, Any], clinical_context: Any) -> dict[str, Any]:
    digoxin_level = _find_number(labs, {"digoxin", "digoxinLevel", "serumDigoxin"})
    combined = _flatten_text({"labs": labs, "context": clinical_context}).lower()

    symptoms = [
        symptom
        for symptom in ("nausea", "vomiting", "confusion", "visual changes", "yellow vision", "blurred vision")
        if symptom in combined
    ]

    exposure = medication_context.get("digoxinExposure")
    if exposure is None:
        digoxin_supported: bool | None = None
        reduced_clearance: bool | None = None
    elif exposure is False:
        digoxin_supported = False
        reduced_clearance = False
    else:
        reduced_clearance = bool(
            renal_context.get("acuteKidneyInjurySupported")
            or renal_context.get("esrd")
            or renal_context.get("creatinine") is not None
        )
        digoxin_supported = bool(
            digoxin_level is not None
            or reduced_clearance
            or symptoms
        )

    return {
        "available": exposure is not None or digoxin_level is not None,
        "digoxinLevel": digoxin_level,
        "symptoms": symptoms,
        "digoxinToxicitySupported": digoxin_supported,
        "reducedClearanceSupported": reduced_clearance,
    }


def _extract_lvef_pct(*values: Any) -> float | None:
    combined = _flatten_text(values)
    patterns = (
        r"\bLVEF\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*%",
        r"\bEF\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*%",
        r"left ventricular ejection fraction\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*%",
        r"ejection fraction\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*%",
    )

    for pattern in patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            value = _number(match.group(1))
            if value is not None and 0 <= value <= 100:
                return value

    return None


def _patient_context(patient: dict[str, Any], clinical_context: Any = None) -> dict[str, Any]:
    lvef = (
        _number(patient.get("leftVentricularEjectionFraction"))
        or _number(patient.get("lvef"))
        or _find_number(patient, {"lvef", "leftVentricularEjectionFraction"})
        or _extract_lvef_pct(patient, clinical_context)
    )

    evidence = {"patient": patient, "context": clinical_context}
    prior_afib: bool | None = None
    if _phrase_asserted(
        evidence,
        ("history of atrial fibrillation", "prior atrial fibrillation", "known atrial fibrillation", "chronic afib"),
    ):
        prior_afib = True
    if _phrase_asserted(
        evidence,
        ("no prior atrial fibrillation", "no history of atrial fibrillation", "new-onset atrial fibrillation", "new onset afib"),
    ):
        prior_afib = False

    return {
        "available": bool(patient),
        "age": patient.get("age"),
        "sex": patient.get("sex") or patient.get("gender"),
        "primaryDiagnosis": patient.get("primaryDiagnosis"),
        "history": patient.get("history") or [],
        "leftVentricularEjectionFraction": lvef,
        "priorAtrialFibrillation": prior_afib,
    }


def _structural_context(patient_context: dict[str, Any], clinical_context: Any) -> dict[str, Any]:
    combined = _flatten_text({"patient": patient_context, "context": clinical_context}).lower()
    findings: list[str] = []

    for phrase, label in (
        ("ischemic cardiomyopathy", "Ischemic cardiomyopathy"),
        ("dilated cardiomyopathy", "Dilated cardiomyopathy"),
        ("prior myocardial infarction", "Prior myocardial infarction"),
        ("prior inferior mi", "Prior inferior myocardial infarction"),
        ("prior infarct", "Prior myocardial infarction"),
        ("myocardial scar", "Myocardial scar"),
        ("heart failure", "Heart failure"),
    ):
        if phrase in combined and label not in findings:
            findings.append(label)

    lvef = _number(patient_context.get("leftVentricularEjectionFraction"))
    if lvef is not None:
        findings.append(f"LVEF {lvef:g}%")

    return {
        "available": bool(findings),
        "findings": findings,
        "lvefPct": lvef,
        "severeLvDysfunction": (
            lvef <= 30
            if lvef is not None
            else None
        ),
    }



def _coverage_requirements(
    contexts: dict[str, Any],
    scenario_id: str,
) -> list[dict[str, Any]]:
    requirements: list[
        dict[str, Any]
    ] = []

    scenario_required = {
        "VFIB-STEMI-001": {
            "rhythmEvidence",
            "hemodynamicContext",
            "ischemiaInterpretation",
        },
        "TORSADES-LQT-002": {
            "rhythmEvidence",
            "electrolyteInterpretation",
            "qtContext",
            "medicationContext",
        },
        "VT-ISCHEMIC-003": {
            "rhythmEvidence",
            "structuralSubstrate",
            "hemodynamicContext",
            "ischemiaInterpretation",
        },
        "AFIB-RVR-SEPSIS-004": {
            "rhythmEvidence",
            "hemodynamicContext",
            "infectionContext",
        },
        "CHB-HYPERK-005": {
            "rhythmEvidence",
            "hemodynamicContext",
            "electrolyteInterpretation",
            "renalContext",
        },
        "BRADY-DIGTOX-006": {
            "rhythmEvidence",
            "hemodynamicContext",
            "medicationToxicityContext",
            "medicationContext",
            "renalContext",
        },
        "SVT-PSVT-007": {
            "rhythmEvidence",
            "hemodynamicContext",
        },
        "NSVT-ECTOPY-008": {
            "rhythmEvidence",
            "structuralSubstrate",
            "electrolyteInterpretation",
        },
    }.get(
        scenario_id,
        {"rhythmEvidence"},
    )

    def add(
        identifier: str,
        instruction: str,
        match_terms: list[str],
        match_all_groups: (
            list[list[str]]
            | None
        ) = None,
        *,
        clinically_applicable: bool,
        evidence_available: bool,
        materially_relevant: bool,
    ) -> None:
        terms = [
            term
            for term in match_terms
            if term
        ]
        groups = [
            [
                term
                for term in group
                if term
            ]
            for group
            in (
                match_all_groups
                or []
            )
            if any(
                term
                for term in group
            )
        ]
        required = bool(
            identifier
            in scenario_required
            and clinically_applicable
            and evidence_available
            and materially_relevant
        )

        if not (
            terms
            or groups
            or required
        ):
            return

        requirements.append(
            {
                "id": identifier,
                "instruction":
                    instruction,
                "matchTerms": terms,
                "matchAllGroups":
                    groups,
                "clinicallyApplicable":
                    clinically_applicable,
                "evidenceAvailable":
                    evidence_available,
                "materiallyRelevantToEtiology":
                    materially_relevant,
                "requiredInResponse":
                    required,
            }
        )

    rhythm = contexts[
        "rhythmFeatures"
    ]
    add(
        "rhythmEvidence",
        (
            "Use the supplied controlled-"
            "event rate, regularity, QRS, "
            "atrial activity, AV association, "
            "or morphology evidence."
        ),
        [
            str(
                rhythm.get(
                    "ventricularRateBpm"
                )
                or ""
            ),
            str(
                rhythm.get(
                    "regularity"
                )
                or ""
            ),
            *[
                str(item)
                for item in (
                    rhythm.get(
                        "findings"
                    )
                    or []
                )
            ],
            str(
                rhythm.get(
                    "atrioventricularAssociation"
                )
                or ""
            ),
        ],
        clinically_applicable=True,
        evidence_available=True,
        materially_relevant=True,
    )

    structural = contexts[
        "structuralHeartContext"
    ]
    add(
        "structuralSubstrate",
        (
            "Explain how the supplied "
            "structural-heart substrate "
            "relates to the episode."
        ),
        [
            str(item)
            for item in (
                structural.get(
                    "findings"
                )
                or []
            )
        ],
        clinically_applicable=(
            scenario_id
            in {
                "VFIB-STEMI-001",
                "VT-ISCHEMIC-003",
                "NSVT-ECTOPY-008",
            }
        ),
        evidence_available=bool(
            structural.get(
                "available"
            )
        ),
        materially_relevant=bool(
            structural.get(
                "findings"
            )
            or structural.get(
                "severeLvDysfunction"
            )
            is True
        ),
    )

    hemodynamic = contexts[
        "hemodynamicStatus"
    ]
    add(
        "hemodynamicContext",
        (
            "Interpret the supplied "
            "controlled-event hemodynamic "
            "state."
        ),
        [
            str(
                hemodynamic.get(
                    "classification"
                )
                or ""
            ),
            str(
                hemodynamic.get(
                    "systolicBloodPressure"
                )
                or ""
            ),
            str(
                hemodynamic.get(
                    "map"
                )
                or ""
            ),
            (
                "pulseless"
                if hemodynamic.get(
                    "pulseless"
                )
                is True
                else ""
            ),
        ],
        clinically_applicable=True,
        evidence_available=bool(
            hemodynamic.get(
                "available"
            )
        ),
        materially_relevant=(
            hemodynamic.get(
                "classification"
            )
            in {
                "borderline",
                "unstable",
                "cardiac_arrest",
            }
            or hemodynamic.get(
                "pulseless"
            )
            is True
            or hemodynamic.get(
                "shockSupported"
            )
            is True
        ),
    )

    electrolytes = contexts[
        "electrolyteContext"
    ]
    abnormal = list(
        electrolytes.get(
            "abnormalElectrolytes"
        )
        or []
    )
    add(
        "electrolyteInterpretation",
        (
            "Explain whether supplied "
            "electrolyte values support or "
            "argue against an electrolyte "
            "contribution."
        ),
        [
            "potassium",
            "magnesium",
            "electrolyte",
            *abnormal,
            (
                "within reference"
                if (
                    electrolytes.get(
                        "electrolyteAbnormalityPresent"
                    )
                    is False
                )
                else "abnormal"
            ),
        ],
        clinically_applicable=(
            scenario_id
            in {
                "TORSADES-LQT-002",
                "VT-ISCHEMIC-003",
                "CHB-HYPERK-005",
                "BRADY-DIGTOX-006",
                "NSVT-ECTOPY-008",
            }
        ),
        evidence_available=bool(
            electrolytes.get(
                "available"
            )
        ),
        materially_relevant=(
            electrolytes.get(
                "electrolyteAbnormalityPresent"
            )
            is not None
        ),
    )

    ischemia = contexts[
        "ischemiaContext"
    ]
    add(
        "ischemiaInterpretation",
        (
            "Interpret supplied chest-pain, "
            "ST-segment, and troponin "
            "evidence."
        ),
        [
            "troponin",
            "ischemia",
            "stemi",
            "chest pain",
            "demand",
        ],
        clinically_applicable=(
            scenario_id
            in {
                "VFIB-STEMI-001",
                "VT-ISCHEMIC-003",
            }
        ),
        evidence_available=bool(
            ischemia.get(
                "available"
            )
        ),
        materially_relevant=any(
            ischemia.get(key)
            is not None
            for key in (
                "acuteStemiSupported",
                "troponinValue",
                "acuteChestPain",
                "stElevationSupported",
            )
        ),
    )

    infection = contexts[
        "infectionContext"
    ]
    add(
        "infectionContext",
        (
            "Interpret supplied infection "
            "or sepsis evidence."
        ),
        [
            "infection",
            "sepsis",
            "fever",
            "wbc",
            "procalcitonin",
            "lactate",
        ],
        clinically_applicable=(
            scenario_id
            == "AFIB-RVR-SEPSIS-004"
        ),
        evidence_available=bool(
            infection.get(
                "available"
            )
        ),
        materially_relevant=(
            infection.get(
                "infectionSupported"
            )
            is not None
            or infection.get(
                "sepsisSupported"
            )
            is not None
        ),
    )

    qt_context = contexts[
        "qtContext"
    ]
    add(
        "qtContext",
        (
            "Interpret QT duration and "
            "relevant medication exposure."
        ),
        [
            "qt",
            "qtc",
            "long-qt",
            "prolonged",
            *[
                str(item)
                for item in (
                    qt_context.get(
                        "qtProlongingMedications"
                    )
                    or []
                )
            ],
        ],
        clinically_applicable=(
            scenario_id
            == "TORSADES-LQT-002"
        ),
        evidence_available=bool(
            qt_context.get(
                "available"
            )
        ),
        materially_relevant=(
            qt_context.get(
                "prolonged"
            )
            is not None
            or bool(
                qt_context.get(
                    "qtProlongingMedications"
                )
            )
        ),
    )

    renal = contexts[
        "renalContext"
    ]
    add(
        "renalContext",
        (
            "Interpret renal function, "
            "dialysis status, or reduced "
            "drug clearance when relevant."
        ),
        [
            "renal",
            "creatinine",
            "dialysis",
            "esrd",
            "clearance",
        ],
        clinically_applicable=(
            scenario_id
            in {
                "CHB-HYPERK-005",
                "BRADY-DIGTOX-006",
            }
        ),
        evidence_available=bool(
            renal.get(
                "available"
            )
        ),
        materially_relevant=any(
            renal.get(key)
            is not None
            for key in (
                "esrd",
                "missedDialysis",
                "acuteKidneyInjurySupported",
                "renalImpairmentSupported",
            )
        ),
    )

    toxicity = contexts[
        "toxicityContext"
    ]
    add(
        "medicationToxicityContext",
        (
            "Interpret medication exposure, "
            "level, clearance, and toxicity "
            "evidence."
        ),
        [
            "digoxin",
            "toxicity",
            "clearance",
            *[
                str(item)
                for item in (
                    toxicity.get(
                        "symptoms"
                    )
                    or []
                )
            ],
        ],
        clinically_applicable=(
            scenario_id
            == "BRADY-DIGTOX-006"
        ),
        evidence_available=bool(
            toxicity.get(
                "available"
            )
        ),
        materially_relevant=(
            toxicity.get(
                "digoxinToxicitySupported"
            )
            is not None
            or toxicity.get(
                "reducedClearanceSupported"
            )
            is not None
        ),
    )

    medication = contexts[
        "medicationContext"
    ]
    relevant_medication = bool(
        (
            scenario_id
            == "TORSADES-LQT-002"
            and medication.get(
                "qtProlongingMedications"
            )
        )
        or (
            scenario_id
            == "BRADY-DIGTOX-006"
            and medication.get(
                "digoxinExposure"
            )
            is not None
        )
        or (
            scenario_id
            == "CHB-HYPERK-005"
            and medication.get(
                "potassiumRaisingMedicationExposure"
            )
            is not None
        )
    )
    add(
        "medicationContext",
        (
            "Use clinically relevant "
            "medication exposure without "
            "inventing treatment "
            "recommendations."
        ),
        [
            *[
                str(item)
                for item in (
                    medication.get(
                        "qtProlongingMedications"
                    )
                    or []
                )
            ],
            *[
                str(item)
                for item in (
                    medication.get(
                        "homeMedications"
                    )
                    or []
                )
            ],
            *[
                str(item)
                for item in (
                    medication.get(
                        "activeMedications"
                    )
                    or []
                )
            ],
            (
                "digoxin"
                if medication.get(
                    "digoxinExposure"
                )
                is True
                else ""
            ),
        ],
        clinically_applicable=(
            scenario_id
            in {
                "TORSADES-LQT-002",
                "BRADY-DIGTOX-006",
                "CHB-HYPERK-005",
            }
        ),
        evidence_available=bool(
            medication.get(
                "available"
            )
        ),
        materially_relevant=(
            relevant_medication
        ),
    )

    return requirements


def _value_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "yes" if value else "no"

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        raw_value = value.get("value") if "value" in value else None
        unit = _text(value.get("unit"))
        note = _text(value.get("note") or value.get("status") or value.get("flag"))

        if raw_value is not None:
            parts = [str(raw_value)]
            if unit:
                parts.append(unit)
            if note:
                parts.append(f"({note})")
            return " ".join(parts)

        return "; ".join(
            f"{key}: {_value_text(item)}"
            for key, item in value.items()
            if _value_text(item)
        )

    if isinstance(value, list):
        return "; ".join(filter(None, (_value_text(item) for item in value)))

    return str(value)


def _section(title: str, value: Any) -> list[str]:
    lines: list[str] = []

    if isinstance(value, dict):
        for key, item in value.items():
            if key == "available" or item is None or item == [] or item == {}:
                continue
            text = _value_text(item)
            if text:
                lines.append(f"- {key}: {text}")
    else:
        text = _value_text(value)
        if text:
            lines.append(f"- {text}")

    return [title, *lines, ""] if lines else []


def normalize_scenario_evidence(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    scenario_payload: dict[str, Any],
    capture_evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    episode = dict(scenario_payload.get("episode") or {})
    patient = dict(scenario_payload.get("patient") or {})
    vitals = dict(scenario_payload.get("vitals") or {})
    labs = dict(scenario_payload.get("labs") or {})
    clinical_context = scenario_payload.get("clinicalContext") or {}
    oracle_patient_context = dict(
        scenario_payload.get("oraclePatientContext") or {}
    )
    if oracle_patient_context:
        oracle_patient_context = {
            "available": True,
            "sourceType": "oracle_smart_fhir",
            "pairedForResearchEvaluation": True,
            **oracle_patient_context,
        }
    else:
        oracle_patient_context = {
            "available": False,
            "sourceType": "oracle_smart_fhir",
        }
    measurements = dict((scenario_payload.get("ecg") or {}).get("measurements") or {})

    diagnosis_definition = (
        SCENARIO_DIAGNOSIS_MAP.get(
            scenario_id
        )
    )

    display = (
        (
            diagnosis_definition
            or {}
        ).get("display")
        or _text(
            episode.get("display")
        )
        or _text(
            measurements.get("rhythm")
        )
        or _text(
            episode.get("diagnosis")
        )
        or _text(
            episode.get("type")
        )
        or scenario_id
    )

    rhythm_features = _canonical_rhythm_features(measurements, episode, clinical_context)
    patient_context = _patient_context(patient, clinical_context)
    structural_context = _structural_context(patient_context, clinical_context)
    hemodynamic = _hemodynamic_context(vitals, clinical_context)
    electrolytes = _electrolyte_context(labs)
    renal = _renal_context(labs, patient, clinical_context)
    ischemia = _ischemia_context(labs, clinical_context, measurements)
    infection = _infection_context(labs, vitals, clinical_context)
    medications = _medication_context(patient, scenario_payload, clinical_context)
    qt_context = _qt_context(rhythm_features, medications, clinical_context)
    toxicity = _toxicity_context(labs, medications, renal, clinical_context)

    detector = {
        "ruleId": capture_evidence.get("detectorRuleId"),
        "estimatedRateBpm": _number(capture_evidence.get("detectorRateBpm")),
        "triggerLatencySeconds": _number(capture_evidence.get("triggerLatencySeconds")),
        "referenceOnsetOffsetSeconds": _number(capture_evidence.get("referenceOnsetOffsetSeconds")),
        "detectedTriggerOffsetSeconds": _number(capture_evidence.get("detectedTriggerOffsetSeconds")),
    }
    capture = {
        "durationSeconds": _number(capture_evidence.get("captureDurationSeconds")),
        "preSeconds": _number(capture_evidence.get("preSecondsCaptured")),
        "eventSeconds": _number(capture_evidence.get("eventDurationSeconds")),
        "postSeconds": _number(capture_evidence.get("postSecondsCaptured")),
        "complete": bool((capture_evidence.get("captureCompleteness") or {}).get("captureComplete")),
        "sampleRateHz": _number(capture_evidence.get("sampleRateHz")),
        "leads": list(capture_evidence.get("leads") or []),
    }

    diagnostic_event = {
        "schemaVersion": "diagnostic-event-universal-v2",
        "eventDetected": True,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "diagnosis": {
            "code": (
                (
                    diagnosis_definition
                    or {}
                ).get("code")
                or _diagnosis_code(
                    episode.get("type"),
                    display,
                    measurements.get(
                        "rhythm"
                    ),
                    scenario_id,
                )
            ),
            "display": display,
            "authoritative": True,
        },
        "source": {
            "type": "controlled_evaluation_scenario",
            "name": "SLM_Eval",
            "identifier": scenario_id,
            "futureReplacement": "ecg_ml_diagnostic_model",
        },
        "confidence": {"value": 1.0, "basis": "controlled_evaluation_label"},
        "measurements": {
            key: rhythm_features.get(key)
            for key in (
                "ventricularRateBpm",
                "atrialRateBpm",
                "qrsDurationMs",
                "qtcMs",
                "prIntervalMs",
                "regularity",
                "axisDegrees",
                "pWavePresent",
                "atrialActivityPresent",
                "atrioventricularAssociation",
            )
        },
        "rhythmFeatures": rhythm_features,
        "hemodynamicStatus": hemodynamic,
        "electrolyteContext": electrolytes,
        "ischemiaContext": ischemia,
        "infectionContext": infection,
        "qtContext": qt_context,
        "renalContext": renal,
        "medicationContext": medications,
        "toxicityContext": toxicity,
        "patientContext": patient_context,
        "structuralHeartContext": structural_context,
        "laboratoryContext": labs,
        "recentClinicalContext": clinical_context,
        "pairedOraclePatientContext": oracle_patient_context,
        "detector": detector,
        "capture": capture,
        "provenance": {
            "isIndependentDiagnosis": False,
            "evaluationControlled": True,
            "diagnosisOwnedByUpstreamSource": True,
            "timingOwnedByDeterministicDetector": True,
            "slmMayReclassify": False,
        },
    }

    contexts = {
        "rhythmFeatures": rhythm_features,
        "patientContext": patient_context,
        "structuralHeartContext": structural_context,
        "hemodynamicStatus": hemodynamic,
        "electrolyteContext": electrolytes,
        "ischemiaContext": ischemia,
        "infectionContext": infection,
        "qtContext": qt_context,
        "renalContext": renal,
        "medicationContext": medications,
        "toxicityContext": toxicity,
        "laboratoryContext": labs,
        "recentClinicalContext": clinical_context,
        "oraclePatientContext": oracle_patient_context,
        "detector": detector,
        "capture": capture,
    }

    coverage_requirements = _coverage_requirements(
        contexts,
        scenario_id,
    )

    flat_parts: list[str] = [
        "AUTHORITATIVE DIAGNOSIS",
        f"- code: {diagnostic_event['diagnosis']['code']}",
        f"- display: {display}",
        "",
    ]

    for title, key in (
        ("RHYTHM MEASUREMENTS AND FEATURES", "rhythmFeatures"),
        ("PATIENT CONTEXT", "patientContext"),
        ("STRUCTURAL HEART CONTEXT", "structuralHeartContext"),
        ("HEMODYNAMIC CONTEXT", "hemodynamicStatus"),
        ("ELECTROLYTE CONTEXT", "electrolyteContext"),
        ("RENAL CONTEXT", "renalContext"),
        ("ISCHEMIA CONTEXT", "ischemiaContext"),
        ("INFECTION CONTEXT", "infectionContext"),
        ("QT CONTEXT", "qtContext"),
        ("MEDICATION CONTEXT", "medicationContext"),
        ("TOXICITY CONTEXT", "toxicityContext"),
        ("LABORATORY CONTEXT", "laboratoryContext"),
        ("RECENT CLINICAL CONTEXT", "recentClinicalContext"),
        ("PAIRED ORACLE SMART FHIR CONTEXT", "oraclePatientContext"),
        ("DETECTOR CONTEXT", "detector"),
        ("CAPTURE CONTEXT", "capture"),
    ):
        flat_parts.extend(_section(title, contexts[key]))

    required_coverage = [
        item for item in coverage_requirements
        if item.get("requiredInResponse") is True
    ]
    optional_coverage = [
        item for item in coverage_requirements
        if item.get("requiredInResponse") is not True
    ]

    if required_coverage:
        flat_parts.append("REQUIRED EVIDENCE COVERAGE")
        flat_parts.extend(f"- {item['instruction']}" for item in required_coverage)
        flat_parts.append("")

    if optional_coverage:
        flat_parts.append("OPTIONAL CONTEXT TO CONSIDER WHEN CLINICALLY RELEVANT")
        flat_parts.extend(f"- {item['instruction']}" for item in optional_coverage)
        flat_parts.append("")

    evidence_bundle = {
        "schemaVersion": "grounded-evidence-universal-v2",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "demoRunId": capture_evidence.get("demoRunId"),
        "authoritativeDiagnosis": diagnostic_event["diagnosis"],
        "sourceSeparation": {
            "authoritativeDiagnosisSource": "controlled_evaluation_scenario",
            "pairedClinicalContextSource": (
                "oracle_smart_fhir"
                if oracle_patient_context.get("available")
                else "slm_eval_scenario"
            ),
            "oracleContextDoesNotOwnDiagnosis": True,
            "causalClaimsRequireDirectSupport": True,
        },
        **contexts,
        "availability": {
            key: bool(value.get("available"))
            for key, value in contexts.items()
            if isinstance(value, dict) and "available" in value
        },
        "coverageRequirements": coverage_requirements,
        "contexts": contexts,
        "flatEvidence": "\n".join(flat_parts).strip(),
    }

    return diagnostic_event, evidence_bundle


def rebuild_from_saved_input(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostic_event = record.get("diagnosticEvent") or {}
    evidence_bundle = record.get("evidenceBundle") or record.get("suppliedEvidence") or {}

    if (
        diagnostic_event.get("schemaVersion") == "diagnostic-event-universal-v2"
        and evidence_bundle.get("schemaVersion") == "grounded-evidence-universal-v2"
    ):
        return diagnostic_event, evidence_bundle

    scenario_id = str(
        record.get("scenarioId")
        or (diagnostic_event.get("source") or {}).get("identifier")
        or ""
    )
    episode_id = str(record.get("episodeId") or diagnostic_event.get("episodeId") or "")
    incident_id = str(record.get("incidentId") or diagnostic_event.get("incidentId") or "")

    patient = (
        evidence_bundle.get("patientContext")
        or diagnostic_event.get("patientContext")
        or {}
    )
    measurements = (
        evidence_bundle.get("diagnosticMeasurements")
        or diagnostic_event.get("measurements")
        or diagnostic_event.get("rhythmFeatures")
        or {}
    )
    clinical_context = (
        evidence_bundle.get("clinicalContext")
        or evidence_bundle.get("scenarioClinicalContext")
        or diagnostic_event.get("recentClinicalContext")
        or {}
    )

    scenario_payload = {
        "episode": {
            "display": (diagnostic_event.get("diagnosis") or {}).get("display"),
            "type": (diagnostic_event.get("diagnosis") or {}).get("code"),
        },
        "patient": patient,
        "vitals": evidence_bundle.get("episodeTimeVitals") or diagnostic_event.get("hemodynamicStatus") or {},
        "labs": evidence_bundle.get("episodeTimeLabs") or diagnostic_event.get("laboratoryContext") or {},
        "medications": evidence_bundle.get("medications") or (diagnostic_event.get("medicationContext") or {}).get("activeMedications") or [],
        "clinicalContext": clinical_context,
        "oraclePatientContext": (
            evidence_bundle.get("oraclePatientContext")
            or diagnostic_event.get("pairedOraclePatientContext")
            or {}
        ),
        "ecg": {"measurements": measurements},
    }

    capture = diagnostic_event.get("capture") or (evidence_bundle.get("captureContext") or {}).get("capture") or {}
    detector = diagnostic_event.get("detector") or (evidence_bundle.get("captureContext") or {}).get("detector") or {}

    capture_evidence = {
        "captureDurationSeconds": capture.get("durationSeconds"),
        "preSecondsCaptured": capture.get("preSeconds"),
        "eventDurationSeconds": capture.get("eventSeconds"),
        "postSecondsCaptured": capture.get("postSeconds"),
        "captureCompleteness": {"captureComplete": capture.get("complete")},
        "detectorRuleId": detector.get("ruleId"),
        "detectorRateBpm": detector.get("estimatedRateBpm"),
        "triggerLatencySeconds": detector.get("triggerLatencySeconds"),
        "referenceOnsetOffsetSeconds": detector.get("referenceOnsetOffsetSeconds"),
        "detectedTriggerOffsetSeconds": detector.get("detectedTriggerOffsetSeconds"),
        "triggerHeartRate": measurements.get("ventricularRateBpm") or measurements.get("heartRateBpm"),
    }

    return normalize_scenario_evidence(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        scenario_payload=scenario_payload,
        capture_evidence=capture_evidence,
    )


def serialize_normalized_input(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    diagnostic_event: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "schemaVersion": "grounded-model-input-universal-v2",
            "scenarioId": scenario_id,
            "episodeId": episode_id,
            "incidentId": incident_id,
            "diagnosticEvent": diagnostic_event,
            "evidenceBundle": evidence_bundle,
            "recommendedActionsRequired": False,
        },
        indent=2,
        ensure_ascii=False,
    )

# V6.0.3 compact model-facing input builder. Kept here as a re-export so
# existing imports that treat evidence_normalizer.py as the evidence boundary
# can adopt the new clinical-only object without changing the response cue.
from .model_clinical_evidence import build_model_clinical_evidence  # noqa: E402,F401
