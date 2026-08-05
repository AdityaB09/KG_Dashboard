from __future__ import annotations
import math

import hashlib
import json
import os
import re
from typing import Any


FACT_FIELDS = (
    "episodeSummary",
    "detectedEpisodeContext",
    "mostLikelyEtiology",
    "contributingFactors",
    "uncertaintyAndMissingData",
)

# KGEN V6.0.1 Validator Semantics Repair
KGEN_VALIDATOR_SEMANTICS_VERSION = "6.0.1"

# One centralized registry owns rhythm-code normalization, display aliases,
# parent/child compatibility, generic rate descriptions, and mechanisms.
RHYTHM_CONCEPT_REGISTRY: dict[str, dict[str, Any]] = {
    "VENTRICULAR_FIBRILLATION": {
        "codes": ("VENTRICULAR_FIBRILLATION", "VFIB", "VF"),
        "aliases": ("ventricular fibrillation", "vfib", "vf"),
        "category": "rhythm",
    },
    "TORSADES_DE_POINTES": {
        "codes": (
            "TORSADES",
            "TORSADES_DE_POINTES",
            "POLYMORPHIC_VT",
            "POLYMORPHIC_VENTRICULAR_TACHYCARDIA",
        ),
        "aliases": (
            "torsades de pointes",
            "torsades",
            "polymorphic ventricular tachycardia",
            "polymorphic vt",
        ),
        "category": "rhythm",
    },
    "MONOMORPHIC_VENTRICULAR_TACHYCARDIA": {
        "codes": (
            "MONOMORPHIC_VT",
            "MONOMORPHIC_VENTRICULAR_TACHYCARDIA",
        ),
        "aliases": (
            "monomorphic ventricular tachycardia",
            "monomorphic vt",
        ),
        "category": "rhythm",
    },
    "NONSUSTAINED_VENTRICULAR_TACHYCARDIA": {
        "codes": (
            "NSVT",
            "NSVT_ECTOPY",
            "NONSUSTAINED_VT",
            "NONSUSTAINED_VENTRICULAR_TACHYCARDIA",
            "FREQUENT_PVCS_WITH_NSVT_RUN",
        ),
        "aliases": (
            "nonsustained ventricular tachycardia",
            "non-sustained ventricular tachycardia",
            "nonsustained vt",
            "non-sustained vt",
            "nsvt ectopy",
            "nsvt",
            "frequent pvcs with nsvt run",
        ),
        "category": "rhythm",
    },
    "VENTRICULAR_TACHYCARDIA": {
        "codes": ("VENTRICULAR_TACHYCARDIA", "VT"),
        "aliases": ("ventricular tachycardia", "ventricular tach", "vt"),
        "category": "parent_rhythm",
    },
    "JUNCTIONAL_BRADYCARDIA": {
        "codes": (
            "JUNCTIONAL_BRADYCARDIA",
            "JUNCTIONAL_RHYTHM_WITH_BRADYCARDIA",
        ),
        "aliases": (
            "junctional rhythm with bradycardia",
            "junctional bradycardia",
            "slow junctional rhythm",
        ),
        "category": "rhythm",
    },
    "COMPLETE_HEART_BLOCK": {
        "codes": (
            "COMPLETE_HEART_BLOCK",
            "THIRD_DEGREE_AV_BLOCK",
            "COMPLETE_AV_BLOCK",
        ),
        "aliases": (
            "third-degree atrioventricular block",
            "third degree atrioventricular block",
            "third-degree av block",
            "third degree av block",
            "complete atrioventricular block",
            "complete av block",
            "complete heart block",
        ),
        "category": "rhythm",
    },
    "BRADYCARDIA": {
        "codes": ("BRADYCARDIA",),
        "aliases": ("symptomatic bradycardia", "marked bradycardia", "bradycardia"),
        "category": "generic_rate",
    },
    "SINUS_BRADYCARDIA": {
        "codes": ("SINUS_BRADYCARDIA",),
        "aliases": ("sinus bradycardia",),
        "category": "rhythm",
    },
    "PAROXYSMAL_SUPRAVENTRICULAR_TACHYCARDIA": {
        "codes": (
            "PSVT",
            "PAROXYSMAL_SUPRAVENTRICULAR_TACHYCARDIA",
            "SUPRAVENTRICULAR_TACHYCARDIA",
            "SVT",
        ),
        "aliases": (
            "paroxysmal supraventricular tachycardia",
            "supraventricular tachycardia",
            "paroxysmal svt",
            "psvt",
            "svt",
        ),
        "category": "rhythm",
    },
    "AVNRT": {
        "codes": ("AVNRT",),
        "aliases": (
            "atrioventricular nodal reentrant tachycardia",
            "av nodal reentrant tachycardia",
            "avnrt",
        ),
        "category": "mechanism",
    },
    "AVRT": {
        "codes": ("AVRT",),
        "aliases": (
            "atrioventricular reentrant tachycardia",
            "av reentrant tachycardia",
            "avrt",
        ),
        "category": "mechanism",
    },
    "ATRIAL_FIBRILLATION_RVR": {
        "codes": (
            "ATRIAL_FIBRILLATION_RVR",
            "AFIB_RVR",
            "ATRIAL_FIBRILLATION_WITH_RAPID_VENTRICULAR_RESPONSE",
        ),
        "aliases": (
            "atrial fibrillation with rapid ventricular response",
            "atrial fibrillation with rvr",
            "afib with rapid ventricular response",
            "afib with rvr",
            "afib rvr",
        ),
        "category": "rhythm",
    },
    "ATRIAL_FIBRILLATION": {
        "codes": ("ATRIAL_FIBRILLATION", "AFIB", "AF"),
        "aliases": ("atrial fibrillation", "afib", "af"),
        "category": "parent_rhythm",
    },
    "SINUS_TACHYCARDIA": {
        "codes": ("SINUS_TACHYCARDIA",),
        "aliases": ("sinus tachycardia", "sinus tach"),
        "category": "rhythm",
    },
}

RHYTHM_CODE_TO_CONCEPT: dict[str, str] = {
    re.sub(r"[^A-Z0-9]+", "_", str(code).upper()).strip("_"): concept
    for concept, definition in RHYTHM_CONCEPT_REGISTRY.items()
    for code in definition.get("codes", ())
}

# Retained for compatibility with older tests/importers. New contradiction
# semantics are owned exclusively by RHYTHM_CONCEPT_REGISTRY.
RHYTHM_ALIASES: dict[str, tuple[str, ...]] = {
    code: tuple(definition.get("aliases", ()))
    for definition in RHYTHM_CONCEPT_REGISTRY.values()
    for code in definition.get("codes", ())
}

RHYTHM_HISTORICAL_MARKERS = (
    "history of",
    "known history of",
    "past medical history of",
    "prior",
    "previously diagnosed",
    "chronic history of",
    "on medication for",
    "treated for",
)

RHYTHM_CURRENT_ASSERTION_MARKERS = (
    "current rhythm is",
    "current rhythm was",
    "captured episode is",
    "captured episode was",
    "episode represents",
    "episode was",
    "episode is",
    "diagnosis is",
    "diagnosis was",
    "rhythm is",
    "rhythm was",
    "reclassified as",
    "identified as",
    "diagnosed as",
    "most consistent with",
    "instead of",
    "rather than",
)

RHYTHM_POSSIBILITY_MARKERS = (
    "possible",
    "possibly",
    "may represent",
    "may reflect",
    "could represent",
    "could reflect",
    "likely mechanism",
    "possible mechanism",
    "such as",
)

RHYTHM_BASELINE_OR_REMOTE_MARKERS = (
    "baseline",
    "pre-event",
    "before the event",
    "before onset",
    "post-event",
    "after termination",
    "after the episode",
)


def _rhythm_code_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def _canonical_rhythm_concept(value: Any) -> str:
    key = _rhythm_code_key(value)
    return RHYTHM_CODE_TO_CONCEPT.get(key, key)


def _rhythm_alias_pattern(alias: str) -> str:
    escaped = re.escape(_normalize(alias))
    return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"


def _rhythm_clause_parts(text: str) -> list[str]:
    output: list[str] = []
    for sentence in _sentences(text):
        for part in re.split(
            r"\s*;\s*|\s+\b(?:but|however|whereas|while)\b\s+",
            sentence,
            flags=re.IGNORECASE,
        ):
            clean = part.strip(" -:\t\r\n")
            if clean:
                output.append(clean)
    return output


def _rhythm_mention_is_historical(clause: str, start: int) -> bool:
    normalized = _normalize(clause)
    prefix = normalized[max(0, start - 110):start]
    suffix = normalized[start:start + 90]
    if any(marker in prefix for marker in RHYTHM_HISTORICAL_MARKERS):
        return True
    if re.search(r"\bon\b.{0,55}\bfor\b\s*$", prefix):
        return True
    if re.search(r"\b(?:takes|taking|receives|receiving)\b.{0,55}\bfor\b\s*$", prefix):
        return True
    if re.search(r"\b(?:atrial fibrillation|afib|af)\b.{0,28}\b(?:history|historical|remote)\b", suffix):
        return True
    return False


def _rhythm_mention_is_negated_or_remote(clause: str, alias: str, start: int) -> bool:
    normalized = _normalize(clause)
    prefix = normalized[max(0, start - 55):start]
    negation_pattern = (
        r"(?:^|\b)(?:no|not|without|absence of|negative for|"
        r"rather than|instead of|rules out|ruled out)\s+(?:evidence of\s+)?$"
    )
    if re.search(negation_pattern, prefix):
        return True
    if any(marker in normalized for marker in RHYTHM_BASELINE_OR_REMOTE_MARKERS):
        return True
    return False


def _rhythm_clause_is_current_assertion(clause: str, start: int) -> bool:
    normalized = _normalize(clause)
    local = normalized[max(0, start - 100): start + 120]
    if any(marker in local for marker in RHYTHM_CURRENT_ASSERTION_MARKERS):
        return True
    if re.search(
        r"\b(?:this|the)\s+(?:episode|rhythm|capture|tracing)\s+(?:is|was|shows|demonstrates|represents)\b",
        local,
    ):
        return True
    return False


def _rhythm_concepts_compatible(
    authoritative: str,
    mentioned: str,
    clause: str,
) -> bool:
    if authoritative == mentioned:
        return True

    ventricular_family = {
        "TORSADES_DE_POINTES",
        "MONOMORPHIC_VENTRICULAR_TACHYCARDIA",
        "NONSUSTAINED_VENTRICULAR_TACHYCARDIA",
    }
    if authoritative in ventricular_family and mentioned == "VENTRICULAR_TACHYCARDIA":
        return True
    if mentioned in ventricular_family and authoritative == "VENTRICULAR_TACHYCARDIA":
        return True

    if {
        authoritative,
        mentioned,
    } <= {"ATRIAL_FIBRILLATION", "ATRIAL_FIBRILLATION_RVR"}:
        return True

    if authoritative in {"JUNCTIONAL_BRADYCARDIA", "COMPLETE_HEART_BLOCK"} and mentioned == "BRADYCARDIA":
        return True

    if authoritative == "PAROXYSMAL_SUPRAVENTRICULAR_TACHYCARDIA" and mentioned in {"AVNRT", "AVRT"}:
        normalized = _normalize(clause)
        return any(marker in normalized for marker in RHYTHM_POSSIBILITY_MARKERS)

    return False

NEGATION_MARKERS = (
    "no ",
    "not ",
    "without ",
    "denies ",
    "absence of ",
    "no evidence of ",
    "unlikely ",
    "less likely ",
    "does not show ",
    "does not support ",
    "argues against ",
    "rather than ",
    "excluded ",
)

BASELINE_MARKERS = (
    "baseline",
    "pre-event",
    "before the event",
    "before onset",
    "post-event",
    "after termination",
    "after the episode",
)

INSTRUCTION_LEAKAGE_PHRASES = (
    "this diagnosis is authoritative",
    "authoritative for this task",
    "you are not diagnosing",
    "you must not diagnose",
    "you must not reclassify",
    "do not reclassify",
    "return json only",
    "use exactly the schema",
    "your task is limited to",
    "field requirements",
    "coverage requirements",
)

MISSING_LANGUAGE = (
    "missing",
    "not provided",
    "not available",
    "unavailable",
    "lack of",
    "lacks",
    "unknown",
    "no information",
    "insufficient information",
)

WATCHED_FACT_TERMS = (
    "diabetes",
    "hypertension",
    "hyperlipidemia",
    "obesity",
    "metformin",
    "insulin",
    "aspirin",
    "nitroglycerin",
    "stress test",
    "fever",
    "sepsis",
    "dialysis",
    "digoxin",
    "syncope",
)

TREATMENT_RECOMMENDATION_PATTERNS = (
    r"\b(recommend|recommended|should receive|should be given|should undergo|must receive)\b",
    r"\b(administer|start|initiate|give|treat with|cardiovert|defibrillate|perform dialysis)\b",
    r"\b(next step|management plan|immediate action)\b",
)

GENERIC_ETIOLOGIES = {
    "heart disease",
    "cardiac disease",
    "arrhythmia",
    "infection",
    "electrolyte imbalance",
    "medication effect",
    "unknown",
    "unclear",
    "multifactorial",
}


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def _fact_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ". ".join(
            item
            for item in (_fact_value_text(entry) for entry in value)
            if item
        )
    if isinstance(value, dict):
        return ". ".join(
            item
            for item in (_fact_value_text(entry) for entry in value.values())
            if item
        )
    return str(value)


def _fact_text(response: dict[str, Any]) -> str:
    return ". ".join(
        item
        for item in (_fact_value_text(response.get(field)) for field in FACT_FIELDS)
        if item
    )


def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if item.strip()
    ]


def _is_negated_or_contextual(sentence: str, phrase: str) -> bool:
    normalized = _normalize(sentence)
    normalized_phrase = _normalize(phrase)
    position = normalized.find(normalized_phrase)
    if position < 0:
        return False

    prefix = normalized[max(0, position - 80):position]
    if any(marker in prefix for marker in NEGATION_MARKERS):
        return True
    return any(marker in normalized for marker in BASELINE_MARKERS)


def _positive_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(text):
        normalized = _normalize(sentence)
        for phrase in phrases:
            if _normalize(phrase) not in normalized:
                continue
            if not _is_negated_or_contextual(sentence, phrase):
                return True
    return False


def _is_ordinary_clinical_numeric_token(
    text: str,
    start: int,
    end: int,
) -> bool:
    """Ignore numbers that are labels/classifications, not patient measurements."""
    window = _normalize(
        text[max(0, start - 24): min(len(text), end + 24)]
    )
    patterns = (
        r"\b12[- ]lead(?:\s+ecg)?\b",
        r"\bstage\s+[1-5]\s+(?:ckd|chronic kidney disease)\b",
        r"\btype\s+[12]\s+diabetes(?: mellitus)?\b",
        r"\b(?:post[- ]?operative\s+)?day\s+\d+\b",
        r"\bv[1-6](?:\s*[-–]\s*v[1-6])?\b",
    )
    return any(re.search(pattern, window) for pattern in patterns)


def _supported_numbers(evidence: dict[str, Any]) -> list[float]:
    return _v601_numeric_values_from_model_visible_evidence(evidence)


def _unsupported_numbers(text: str, evidence: dict[str, Any]) -> list[str]:
    supported = _supported_numbers(evidence)
    output: list[str] = []

    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", text):
        token = match.group(0)
        if _v601_nonclinical_numeric_label(text, match.start(), match.end()):
            continue
        number = float(token)
        if _v601_exact_numeric_match(number, supported):
            continue
        if token not in output:
            output.append(token)
    return output[:12]


def _diagnosis_contradictions(facts: str, diagnostic_event: dict[str, Any]) -> list[str]:
    authoritative_code = str((diagnostic_event.get("diagnosis") or {}).get("code") or "")
    authoritative = _canonical_rhythm_concept(authoritative_code)
    output: list[str] = []

    alias_entries: list[tuple[int, str, str]] = []
    for concept, definition in RHYTHM_CONCEPT_REGISTRY.items():
        for alias in definition.get("aliases", ()):
            alias_entries.append((len(_normalize(alias)), concept, str(alias)))
    alias_entries.sort(reverse=True)

    for clause in _rhythm_clause_parts(facts):
        normalized_clause = _normalize(clause)
        occupied: list[tuple[int, int]] = []
        for _, concept, alias in alias_entries:
            for match in re.finditer(_rhythm_alias_pattern(alias), normalized_clause):
                span = (match.start(), match.end())
                if any(not (span[1] <= used[0] or span[0] >= used[1]) for used in occupied):
                    continue
                occupied.append(span)

                if _rhythm_mention_is_historical(clause, match.start()):
                    continue
                if _rhythm_mention_is_negated_or_remote(clause, alias, match.start()):
                    continue
                if _rhythm_concepts_compatible(authoritative, concept, clause):
                    continue
                if not _rhythm_clause_is_current_assertion(clause, match.start()):
                    continue

                code_label = concept
                message = f"Response introduced a competing current rhythm diagnosis from {code_label}."
                if message not in output:
                    output.append(message)

    return output


def _measurement_contradictions(facts: str, diagnostic_event: dict[str, Any]) -> list[str]:
    output: list[str] = []
    normalized = _normalize(facts)
    rhythm = diagnostic_event.get("rhythmFeatures") or diagnostic_event.get("measurements") or {}
    hemodynamics = diagnostic_event.get("hemodynamicStatus") or {}
    electrolytes = diagnostic_event.get("electrolyteContext") or {}
    ischemia = diagnostic_event.get("ischemiaContext") or {}
    infection = diagnostic_event.get("infectionContext") or {}
    qt_context = diagnostic_event.get("qtContext") or {}
    toxicity = diagnostic_event.get("toxicityContext") or {}
    renal = diagnostic_event.get("renalContext") or {}

    if rhythm.get("atrialActivityPresent") is True and _positive_phrase(
        facts,
        ("no p waves", "p waves are absent", "no atrial activity", "atrial activity is absent"),
    ):
        output.append("Response contradicted supplied visible atrial activity.")

    if rhythm.get("atrialActivityPresent") is False and _positive_phrase(
        facts,
        ("p waves are present", "visible p waves", "atrial activity is present"),
    ):
        output.append("Response contradicted supplied absence of atrial activity.")

    if _normalize(rhythm.get("atrioventricularAssociation")) == "dissociated" and _positive_phrase(
        facts,
        ("one-to-one av conduction", "1:1 av conduction", "intact av association"),
    ):
        output.append("Response contradicted supplied atrioventricular dissociation.")

    regularity = _normalize(rhythm.get("regularity"))
    if regularity == "regular" and _positive_phrase(
        facts,
        ("irregularly irregular", "irregular ventricular rhythm", "markedly irregular rhythm"),
    ):
        output.append("Response contradicted the supplied regular rhythm.")
    if regularity == "irregular" and _positive_phrase(
        facts,
        ("regular rhythm", "regular ventricular rhythm", "regular tachycardia"),
    ):
        output.append("Response contradicted the supplied irregular rhythm.")

    qrs = rhythm.get("qrsDurationMs")
    if isinstance(qrs, (int, float)) and qrs >= 120 and _positive_phrase(
        facts,
        ("narrow qrs", "narrow-complex", "narrow complex"),
    ):
        output.append("Response contradicted the supplied wide QRS duration.")
    if isinstance(qrs, (int, float)) and qrs < 120 and _positive_phrase(
        facts,
        ("wide qrs", "wide-complex", "wide complex"),
    ):
        output.append("Response contradicted the supplied narrow QRS duration.")

    classification = _normalize(hemodynamics.get("classification"))
    if classification in {"borderline", "unstable", "cardiac_arrest"} and _positive_phrase(
        facts,
        ("hemodynamically stable", "stable hemodynamics", "no hemodynamic compromise", "clinically stable"),
    ):
        output.append(
            f"Response described stable hemodynamics despite supplied classification '{classification}'."
        )

    pulseless = hemodynamics.get("pulseless")
    if pulseless is False and _positive_phrase(
        facts,
        ("pulseless rhythm", "patient was pulseless", "no pulse", "cardiac arrest"),
    ):
        output.append("Response claimed a pulseless state despite supplied pulseless=false.")
    if pulseless is True and _positive_phrase(
        facts,
        ("maintained a pulse", "pulse was present", "remained perfusing"),
    ):
        output.append("Response claimed a pulse despite supplied pulseless=true.")

    abnormal_electrolytes = electrolytes.get("abnormalElectrolytes") or []
    if electrolytes.get("available") and not abnormal_electrolytes and _positive_phrase(
        facts,
        (
            "severe hypokalemia",
            "severe hyperkalemia",
            "severe hypomagnesemia",
            "electrolyte depletion caused",
            "electrolytes were profoundly abnormal",
        ),
    ):
        output.append(
            "Response invented a major electrolyte abnormality despite supplied values within reference."
        )
    if abnormal_electrolytes and _positive_phrase(
        facts,
        ("electrolytes were normal", "normal potassium and magnesium", "no electrolyte abnormality"),
    ):
        output.append("Response denied documented electrolyte abnormalities.")

    if ischemia.get("acuteStemiSupported") is False and _positive_phrase(
        facts,
        ("acute stemi caused", "acute myocardial infarction caused", "st-elevation myocardial infarction caused"),
    ):
        output.append("Response asserted acute STEMI despite supplied evidence not supporting STEMI.")
    if ischemia.get("acuteStemiSupported") is True and _positive_phrase(
        facts,
        ("no ischemia evidence", "no st elevation", "stemi is not supported", "no acute coronary trigger"),
    ):
        output.append("Response denied supplied acute STEMI evidence.")

    # This check is intentionally positive-assertion only. "No acute chest pain" is allowed.
    if ischemia.get("acuteChestPain") is False and _positive_phrase(
        facts,
        ("presented with chest pain", "ongoing chest pain", "acute chest pain was present", "developed chest pain"),
    ):
        output.append("Response contradicted supplied absence of acute chest pain.")

    if infection.get("infectionSupported") is True and _positive_phrase(
        facts,
        ("no infection evidence", "infection was absent", "no sepsis evidence"),
    ):
        output.append("Response denied supplied infection or sepsis evidence.")
    if infection.get("infectionSupported") is False and _positive_phrase(
        facts,
        ("sepsis caused", "urosepsis caused", "infection precipitated"),
    ):
        output.append("Response attributed the episode to infection despite no supplied infection evidence.")

    if qt_context.get("prolonged") is True and _positive_phrase(
        facts,
        ("qt interval was normal", "normal qtc", "no qt prolongation"),
    ):
        output.append("Response denied supplied QT prolongation.")
    if qt_context.get("prolonged") is False and _positive_phrase(
        facts,
        ("markedly prolonged qtc", "long-qt syndrome", "severe qt prolongation"),
    ):
        output.append("Response invented QT prolongation not supported by the evidence.")

    if toxicity.get("digoxinToxicitySupported") is True and _positive_phrase(
        facts,
        ("no digoxin exposure", "digoxin toxicity is not supported", "normal digoxin level"),
    ):
        output.append("Response denied supplied digoxin-toxicity evidence.")

    if renal.get("missedDialysis") is True and _positive_phrase(
        facts,
        ("dialysis was completed as scheduled", "no missed dialysis", "dialysis adherence was normal"),
    ):
        output.append("Response contradicted supplied missed-dialysis evidence.")

    if renal.get("acuteKidneyInjurySupported") is False and _positive_phrase(
        facts,
        ("acute kidney injury", "aki caused", "new acute renal failure"),
    ):
        output.append("Response asserted acute kidney injury despite supplied AKI=false.")

    patient_context = diagnostic_event.get("patientContext") or {}
    if patient_context.get("priorAtrialFibrillation") is False and _positive_phrase(
        facts,
        (
            "history of atrial fibrillation",
            "prior atrial fibrillation",
            "known atrial fibrillation",
            "chronic atrial fibrillation",
        ),
    ):
        output.append("Response invented a prior history of atrial fibrillation despite supplied priorAtrialFibrillation=false.")

    if ischemia.get("acuteStemiSupported") is False and _positive_phrase(
        facts,
        (
            "acute coronary syndrome caused",
            "acute coronary syndrome was the primary cause",
            "chest pain suggestive of acute coronary syndrome",
            "ongoing acute coronary syndrome",
        ),
    ):
        output.append("Response asserted acute coronary syndrome despite supplied evidence not supporting an acute coronary trigger.")

    if any(
        phrase in normalized
        for phrase in (
            "no significant abnormalities",
            "no important abnormalities",
            "benign episode",
            "clinically benign",
        )
    ) and classification in {"borderline", "unstable", "cardiac_arrest"}:
        output.append("Response minimized the episode despite significant supplied hemodynamic abnormalities.")

    return output


def _field_availability(evidence_bundle: dict[str, Any]) -> dict[str, bool]:
    contexts = evidence_bundle.get("contexts") or {}
    electrolytes = contexts.get("electrolyteContext") or evidence_bundle.get("electrolyteContext") or {}

    patient = contexts.get("patientContext") or evidence_bundle.get("patientContext") or {}
    medications = contexts.get("medicationContext") or evidence_bundle.get("medicationContext") or {}
    hemodynamics = contexts.get("hemodynamicStatus") or evidence_bundle.get("hemodynamicStatus") or {}
    labs = contexts.get("laboratoryContext") or evidence_bundle.get("laboratoryContext") or {}
    infection = contexts.get("infectionContext") or evidence_bundle.get("infectionContext") or {}
    ischemia = contexts.get("ischemiaContext") or evidence_bundle.get("ischemiaContext") or {}
    qt_context = contexts.get("qtContext") or evidence_bundle.get("qtContext") or {}
    renal = contexts.get("renalContext") or evidence_bundle.get("renalContext") or {}

    return {
        "potassium": bool((electrolytes.get("potassium") or {}).get("available")),
        "magnesium": bool((electrolytes.get("magnesium") or {}).get("available")),
        "calcium": bool((electrolytes.get("calcium") or {}).get("available")),
        "sodium": bool((electrolytes.get("sodium") or {}).get("available")),
        "history": bool(patient.get("available") or patient.get("history") or patient.get("primaryDiagnosis")),
        "medications": bool(medications.get("available") or medications.get("homeMedications") or medications.get("activeMedications")),
        "vitals": bool(hemodynamics.get("available") or hemodynamics.get("classification") not in {None, "", "unknown"}),
        "laboratory": bool(labs),
        "infection": bool(infection.get("available")),
        "ischemia": bool(ischemia.get("available")),
        "qt": bool(qt_context.get("available")),
        "renal": bool(renal.get("available")),
    }


def _false_missing_claims(facts: str, evidence_bundle: dict[str, Any]) -> list[str]:
    fields = _field_availability(evidence_bundle)
    aliases = {
        "potassium": ("potassium",),
        "magnesium": ("magnesium",),
        "calcium": ("calcium",),
        "sodium": ("sodium",),
        "history": ("history", "medical history", "patient context", "comorbidities"),
        "medications": ("medication", "medications", "drug history"),
        "vitals": ("vitals", "vital signs", "hemodynamics", "blood pressure"),
        "laboratory": ("labs", "laboratory", "laboratory values"),
        "infection": ("infection data", "sepsis data", "inflammatory markers"),
        "ischemia": ("ischemia data", "troponin data", "chest pain data", "stemi data"),
        "qt": ("qt data", "qtc data"),
        "renal": ("renal data", "kidney data", "creatinine data", "dialysis data"),
    }
    output: list[str] = []

    for sentence in _sentences(facts):
        normalized = _normalize(sentence)
        if not any(marker in normalized for marker in MISSING_LANGUAGE):
            continue

        for field, field_aliases in aliases.items():
            if not fields.get(field):
                continue
            if any(alias in normalized for alias in field_aliases):
                output.append(
                    f"Response incorrectly claimed supplied {field} evidence was missing: {sentence}"
                )

    return list(dict.fromkeys(output))


def _term_matches(normalized_facts: str, term: str) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False

    if normalized_term in normalized_facts:
        return True

    tokens = [token for token in normalized_term.split() if len(token) > 2]
    if not tokens:
        return False

    present = sum(1 for token in tokens if token in normalized_facts)
    return present / len(tokens) >= 0.6


def _semantic_coverage_match(identifier: str, normalized_facts: str) -> bool:
    aliases: dict[str, tuple[tuple[str, ...], ...]] = {
        "rhythmEvidence": (
            ("bpm", "rhythm", "tachycardia", "bradycardia", "fibrillation", "heart block", "qrs"),
        ),
        "structuralSubstrate": (
            ("cardiomyopathy", "myocardial scar", "prior infarct", "myocardial infarction", "lvef", "ejection fraction", "systolic dysfunction", "structural heart"),
        ),
        "hemodynamicContext": (
            ("hypotension", "hypotensive", "borderline", "shock", "vasopressor", "norepinephrine", "blood pressure", "bp ", "map ", "pulseless", "cardiac arrest", "hemodynamic"),
        ),
        "electrolyteInterpretation": (
            ("potassium", "magnesium", "calcium", "electrolyte", "hyperkalemia", "hypokalemia", "hypomagnesemia"),
            ("normal", "within reference", "low", "high", "abnormal", "trigger", "contribute", "less likely", "not supported", "argues against"),
        ),
        "ischemiaInterpretation": (
            ("troponin", "stemi", "st elevation", "chest pain", "ischemia", "acute coronary", "demand"),
        ),
        "infectionContext": (
            ("sepsis", "urosepsis", "infection", "fever", "febrile", "wbc", "leukocytosis", "procalcitonin", "lactate", "bacteremia", "pneumonia"),
        ),
        "qtContext": (
            ("qt", "qtc", "long qt", "prolonged qt", "torsades"),
        ),
        "renalContext": (
            ("renal", "kidney", "creatinine", "dialysis", "esrd", "clearance", "aki"),
        ),
        "medicationToxicityContext": (
            ("digoxin", "toxicity", "toxic", "drug level", "clearance", "yellow vision", "nausea", "vomiting", "confusion"),
        ),
        "medicationContext": (
            ("medication", "drug", "digoxin", "sotalol", "amiodarone", "azithromycin", "ondansetron", "spironolactone", "thiazide", "hydrochlorothiazide"),
        ),
    }

    groups = aliases.get(identifier)
    if not groups:
        return False

    return all(any(alias in normalized_facts for alias in group) for group in groups)


def _coverage_status(facts: str, evidence_bundle: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    normalized = _normalize(facts)
    output: dict[str, bool] = {}
    missing_required: list[str] = []

    for requirement in evidence_bundle.get("coverageRequirements") or []:
        if not isinstance(requirement, dict):
            continue

        identifier = str(requirement.get("id") or "").strip()
        if not identifier:
            continue

        terms = [str(term) for term in requirement.get("matchTerms") or [] if str(term).strip()]
        groups = [
            [str(term) for term in group if str(term).strip()]
            for group in requirement.get("matchAllGroups") or []
            if isinstance(group, list) and group
        ]

        direct_match = any(_term_matches(normalized, term) for term in terms) if terms else False
        group_match = (
            all(any(_term_matches(normalized, term) for term in group) for group in groups)
            if groups
            else False
        )
        semantic_match = _semantic_coverage_match(identifier, normalized)

        matched = semantic_match or group_match or direct_match
        output[identifier] = matched

        if requirement.get("requiredInResponse") is True and not matched:
            missing_required.append(identifier)

    return output, missing_required


def _unsupported_fact_claims(
    facts: str,
    evidence_bundle: dict[str, Any],
    diagnostic_event: dict[str, Any],
) -> list[str]:
    normalized_facts = _normalize(facts)
    normalized_evidence = _normalize(evidence_bundle)
    output: list[str] = []

    for term in WATCHED_FACT_TERMS:
        if term in normalized_facts and term not in normalized_evidence:
            if _positive_phrase(facts, (term,)):
                output.append(f"Unsupported case fact introduced: {term}.")

    diagnosis_code = str((diagnostic_event.get("diagnosis") or {}).get("code") or "")
    if (
        _positive_phrase(facts, ("accessory pathway",))
        and "accessory pathway" not in normalized_evidence
        and diagnosis_code != "SUPRAVENTRICULAR_TACHYCARDIA"
    ):
        output.append("Unsupported case fact introduced: accessory pathway.")

    # Universal causal checks: do not reverse treatment/condition direction.
    if _positive_phrase(
        facts,
        ("volume overload from furosemide", "furosemide caused volume overload", "furosemide contributed to volume overload"),
    ) and "furosemide caused volume overload" not in normalized_evidence:
        output.append("Unsupported causal claim: furosemide caused or worsened volume overload.")

    numbers = _unsupported_numbers(facts, evidence_bundle)
    if numbers:
        output.append("Unsupported numeric claims: " + ", ".join(numbers))

    return output


def _treatment_leakage(facts: str) -> list[str]:
    output: list[str] = []
    for sentence in _sentences(facts):
        normalized = _normalize(sentence)
        if any(re.search(pattern, normalized) for pattern in TREATMENT_RECOMMENDATION_PATTERNS):
            output.append(
                "Treatment recommendation leaked into the etiology/context response: " + sentence
            )
    return output


def _validate_v2_response(
    *,
    response: dict[str, Any],
    diagnostic_event: dict[str, Any],
    supplied_evidence: dict[str, Any],
) -> dict[str, Any]:
    hard_errors: list[str] = []
    quality_errors: list[str] = []
    warnings: list[str] = []
    contradictions: list[str] = []
    unsupported_facts: list[str] = []

    diagnosis = diagnostic_event.get("diagnosis") or {}
    code = str(diagnosis.get("code") or "")
    display = str(diagnosis.get("display") or "").strip()
    facts = _fact_text(response)
    normalized_facts = _normalize(facts)

    for field in ("episodeSummary", "detectedEpisodeContext", "mostLikelyEtiology"):
        if not isinstance(response.get(field), str) or not str(response.get(field)).strip():
            hard_errors.append(f"{field} is missing or empty.")

    for field in ("contributingFactors", "uncertaintyAndMissingData"):
        value = response.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            hard_errors.append(f"{field} must be an array of strings.")

    factors = response.get("contributingFactors")
    if isinstance(factors, list) and len(factors) > 5:
        quality_errors.append(
            "contributingFactors contains more than the contract maximum of five items."
        )

    for phrase in INSTRUCTION_LEAKAGE_PHRASES:
        if phrase in normalized_facts:
            hard_errors.append(f"Prompt instruction leakage detected: '{phrase}'.")

    etiology = _normalize(response.get("mostLikelyEtiology"))
    if etiology in GENERIC_ETIOLOGIES:
        quality_errors.append(
            f"mostLikelyEtiology is too generic: '{response.get('mostLikelyEtiology')}'."
        )

    contradictions.extend(_diagnosis_contradictions(facts, diagnostic_event))
    contradictions.extend(_measurement_contradictions(facts, diagnostic_event))
    hard_errors.extend(_false_missing_claims(facts, supplied_evidence))
    hard_errors.extend(_treatment_leakage(facts))

    unsupported_facts.extend(
        _unsupported_fact_claims(
            facts,
            supplied_evidence,
            diagnostic_event,
        )
    )

    coverage, missing_required = _coverage_status(facts, supplied_evidence)
    if missing_required:
        quality_errors.append(
            "Response did not address required evidence categories: "
            + ", ".join(missing_required)
        )

    hard_errors.extend(contradictions)
    hard_errors.extend(unsupported_facts)

    hard_errors = list(dict.fromkeys(hard_errors))
    quality_errors = list(dict.fromkeys(quality_errors))
    contradictions = list(dict.fromkeys(contradictions))
    unsupported_facts = list(dict.fromkeys(unsupported_facts))

    hard_accepted = not hard_errors
    accepted = hard_accepted and not quality_errors
    displayable = hard_accepted and bool(quality_errors)
    status = (
        "accepted"
        if accepted
        else "accepted_with_review"
        if displayable
        else "rejected"
    )

    return {
        "status": status,
        "accepted": accepted,
        "hardAccepted": hard_accepted,
        "validatorPassed": hard_accepted,
        "displayableWithReview": displayable,
        "qualityReviewRequired": displayable,
        "retryable": bool(hard_errors or missing_required),
        "authoritativeDiagnosisCode": code,
        "authoritativeDiagnosisDisplay": display,
        "errors": [*hard_errors, *quality_errors],
        "hardErrors": hard_errors,
        "qualityErrors": quality_errors,
        "warnings": warnings,
        "contradictions": contradictions,
        "unsupportedFacts": unsupported_facts,
        "evidenceCoverage": coverage,
        "missingRequiredCoverage": missing_required,
        "evidenceCoverageCount": sum(1 for value in coverage.values() if value),
        "evidenceCoverageRequired": sum(
            1
            for requirement in supplied_evidence.get("coverageRequirements") or []
            if isinstance(requirement, dict) and requirement.get("requiredInResponse") is True
        ),
        "recommendedActionsRequired": False,
        "policyVersion": "grounded-response-validator-deterministic-v2.1",
        "groundingStatus": status,
    }


V4_ALLOWED_FIELDS = {
    "episodeSummary",
    "detectedEpisodeContext",
    "mostLikelyEtiology",
    "contributingFactors",
    "uncertaintyAndMissingData",
}

V4_NEGATIVE_CLAIMS: dict[str, tuple[str, ...]] = {
    "structural heart disease": (
        "structural heart disease",
        "cardiomyopathy",
    ),
    "ischemic symptoms": (
        "ischemic symptoms",
        "ischemia",
    ),
    "chest pain": (
        "chest pain",
    ),
    "infection": (
        "infection",
        "sepsis",
        "infectious process",
    ),
    "electrolyte abnormality": (
        "electrolyte abnormality",
        "electrolyte abnormalities",
        "electrolytes",
    ),
    "chronic kidney disease": (
        "chronic kidney disease",
        "ckd",
    ),
    "acute kidney injury": (
        "acute kidney injury",
        "aki",
    ),
    "renal impairment": (
        "renal impairment",
        "kidney impairment",
    ),
}

V4_LOCAL_NEGATION_PREFIXES = (
    "no ",
    "without ",
    "denies ",
    "denied ",
    "absence of ",
    "lack of ",
    "lacking ",
    "negative for ",
)

V4_LOCAL_NEGATION_SUFFIXES = (
    " absent",
    " not present",
    " not documented",
    " not established",
    " not supported",
    " was excluded",
    " were excluded",
    " excluded",
    " unlikely",
)

V4_SOURCE_QUALIFIERS = (
    "controlled-event evidence",
    "controlled event evidence",
    "supplied evidence",
    "available evidence",
    "the evidence",
    "oracle context",
    "oracle fhir context",
    "not supplied",
    "not established",
    "did not support",
    "does not support",
    "was not supported",
    "were not supported",
)


V4_EXPOSURE_WORDS = (
    "taking",
    "receiving",
    "received",
    "administered",
    "on chronic",
    "has been on",
    "exposed to",
    "use of",
    "intake",
)

V4_CAUSAL_WORDS = (
    "caused",
    "due to",
    "triggered",
    "precipitated",
    "most likely etiology",
    "most likely cause",
    "contributed to",
    "responsible for",
)

V4_CURRENT_WORDS = (
    "current",
    "currently",
    "recent",
    "at the time of the event",
    "during the event",
    "episode-time",
    "now",
)

V4_TEMPORAL_QUALIFIERS = (
    "historical",
    "remote",
    "before the event",
    "before event",
    "days before",
    "hours before",
    "months before",
    "years before",
    "not episode-time",
)


def _v4_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _stable_fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _v4_configuration_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    mode = str(evidence.get("clinicalPromptMode") or "")
    if mode not in {
        "oracle_only",
        "controlled_event_plus_oracle",
        "episode_pack_only",
    }:
        errors.append("unsupported_prompt_mode")

    contract = evidence.get("validatorContract") or {}
    if contract.get("schemaVersion") != "validator-contract-v4":
        errors.append("validator_contract_missing_or_invalid")
    if contract.get("clinicalPromptMode") != mode:
        errors.append("validator_prompt_scope_mismatch")
    if contract.get("evidenceFingerprint") != evidence.get("evidenceFingerprint"):
        errors.append("validator_evidence_fingerprint_mismatch")

    linkage = evidence.get("patientLinkage") or {}
    if mode == "episode_pack_only":
        if linkage.get("oracleClinicalContextUsed") is True:
            errors.append("oracle_context_not_excluded")
        manifest = evidence.get("sourceManifest") or {}
        if manifest.get("oracleFhirClinicalContextUsed") is not False:
            errors.append("oracle_context_manifest_invalid")
        oracle = evidence.get("oracleContext") or {}
        if (
            oracle.get("available") is not False
            or oracle.get("excludedByPolicy") is not True
        ):
            errors.append("oracle_context_scope_invalid")
    elif linkage.get("samePatientVerified") is not True:
        errors.append("patient_linkage_failed")

    if not (evidence.get("controlledRhythm") or {}).get("diagnosis"):
        errors.append("controlled_rhythm_missing")
    return errors


def _v4_scoped_diagnostic_event(evidence: dict[str, Any]) -> dict[str, Any]:
    rhythm = evidence.get("controlledRhythm") or {}
    context = evidence.get("controlledEventContext") or {}
    included = bool(context.get("included"))
    return {
        "diagnosis": rhythm.get("diagnosis") or {},
        "rhythmFeatures": rhythm,
        "measurements": rhythm,
        "hemodynamicStatus": (context.get("hemodynamics") or {}) if included else {},
        "electrolyteContext": (context.get("electrolytes") or {}) if included else {},
        "ischemiaContext": (context.get("ischemia") or {}) if included else {},
        "infectionContext": (context.get("infection") or {}) if included else {},
        "qtContext": (context.get("qt") or {}) if included else {},
        "toxicityContext": (context.get("toxicity") or {}) if included else {},
        "renalContext": (context.get("renal") or {}) if included else {},
    }




def _v601_numbers_in_text(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", str(text or "")):
        try:
            value = float(match.group(0))
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _v601_excluded_numeric_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key or "").lower())
    if not normalized:
        return False
    excluded = (
        "fingerprint",
        "checksum",
        "sha256",
        "hash",
        "schema",
        "scenarioid",
        "episodeid",
        "incidentid",
        "identifier",
        "sourcefile",
        "outputdirectory",
        "timestamp",
        "createdat",
        "updatedat",
        "completedat",
        "startedat",
    )
    return any(item in normalized for item in excluded)


def _v601_collect_numeric_values(value: Any, *, key_hint: Any = "") -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        candidate = float(value)
        return [candidate] if math.isfinite(candidate) else []
    if isinstance(value, dict):
        output: list[float] = []
        for key, item in value.items():
            if _v601_excluded_numeric_key(key):
                continue
            output.extend(_v601_collect_numeric_values(item, key_hint=key))
        return output
    if isinstance(value, (list, tuple, set)):
        output: list[float] = []
        for item in value:
            output.extend(_v601_collect_numeric_values(item, key_hint=key_hint))
        return output
    if isinstance(value, str):
        if _v601_excluded_numeric_key(key_hint):
            return []
        return _v601_numbers_in_text(value)
    return []


def _v601_numeric_values_from_model_visible_evidence(evidence: dict[str, Any]) -> list[float]:
    values: list[float] = []

    contract = evidence.get("validatorContract") or {}
    for item in contract.get("numericEvidence") or []:
        if not isinstance(item, dict):
            continue
        values.extend(_v601_collect_numeric_values(item.get("value"), key_hint=item.get("path")))

    # V6.0.1 intentionally indexes the exact scoped evidence envelope used to
    # build the model prompt. Validator-only identity/fingerprint fields are
    # excluded above. This captures controlled-event values, complete episode
    # package values, Phase 6 ranges, signed axes, QT/QTc, vitals, labs, ages,
    # durations, reference ranges, and qualified measurement conflicts.
    for key, item in evidence.items():
        if key == "validatorContract" or _v601_excluded_numeric_key(key):
            continue
        values.extend(_v601_collect_numeric_values(item, key_hint=key))

    output: list[float] = []
    seen: set[str] = set()
    for value in values:
        if not math.isfinite(value):
            continue
        marker = format(value, ".12g")
        if marker in seen:
            continue
        seen.add(marker)
        output.append(value)
    return output


def _v601_exact_numeric_match(number: float, supported: Any) -> bool:
    for item in supported:
        tolerance = max(0.05, abs(float(item)) * 0.001)
        if abs(float(number) - float(item)) <= tolerance:
            return True
    return False


def _v601_nonclinical_numeric_label(text: str, start: int, end: int) -> bool:
    before = _normalize(text[max(0, start - 24):start])
    after = _normalize(text[end:end + 24])
    local = _normalize(text[max(0, start - 24):end + 24])
    if re.search(r"\bphase\s*$", before):
        return True
    if re.search(r"\b(?:type|stage|class|grade|lead|version|v)\s*$", before):
        return True
    if re.search(r"\b(?:run|episode|window|figure|table)\s*$", before) and not re.search(
        r"\b(?:seconds?|ms|bpm|mmhg|mg|dl|mmol|meq|years?|minutes?)\b",
        after,
    ):
        return True
    if re.search(r"\d\s*:\s*\d", local):
        return True
    return False


def _v601_conflict_number(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("value", "median", "mean", "minimum", "maximum"):
            if key in value:
                return _v601_conflict_number(value.get(key))
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        candidate = float(value)
        return candidate if math.isfinite(candidate) else None
    if isinstance(value, str):
        values = _v601_numbers_in_text(value)
        return values[0] if values else None
    return None


def _v601_conflict_clauses(text: str) -> list[str]:
    output: list[str] = []
    for sentence in _sentences(text):
        for part in re.split(
            r"\s*;\s*|\s+\b(?:but|however|whereas|while)\b\s+",
            sentence,
            flags=re.IGNORECASE,
        ):
            clean = part.strip(" -:\t\r\n")
            if clean:
                output.append(clean)
    return output

def _v4_numeric_values(evidence: dict[str, Any]) -> list[float]:
    return _v601_numeric_values_from_model_visible_evidence(evidence)


def _v4_number_supported(
    *,
    number: float,
    text: str,
    start: int,
    evidence: dict[str, Any],
) -> tuple[bool, str | None]:
    supported = _v4_numeric_values(evidence)
    if not supported:
        return False, None

    if _v601_exact_numeric_match(number, supported):
        return True, None

    if not _v4_flag("SLM_NUMERIC_TOLERANCE_ENABLED", False):
        return False, None

    window = _normalize(text[max(0, start - 35): start + 35])
    approximate = any(
        marker in window
        for marker in ("about", "approximately", "around", "roughly", "nearly", "~")
    )
    if approximate:
        for item in supported:
            if abs(number - item) <= max(0.5, abs(item) * 0.02):
                return True, f"Safe approximate numeric paraphrase: {number:g}."

    greater = any(
        marker in window
        for marker in ("over ", "more than ", "greater than ", "at least ")
    )
    if greater:
        for item in supported:
            if item >= number and item <= max(number + 5, number * 1.10):
                return True, f"Supported lower-bound numeric paraphrase: over {number:g}."

    lower = any(
        marker in window
        for marker in ("under ", "less than ", "below ", "at most ")
    )
    if lower:
        for item in supported:
            if item <= number and item >= min(number - 5, number * 0.90):
                return True, f"Supported upper-bound numeric paraphrase: under {number:g}."

    return False, None


def _v4_unsupported_numbers(
    facts: str,
    evidence: dict[str, Any],
) -> tuple[list[str], list[str]]:
    unsupported: list[str] = []
    warnings: list[str] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", facts):
        token = match.group(0)
        if _v601_nonclinical_numeric_label(facts, match.start(), match.end()):
            continue
        number = float(token)
        accepted, warning = _v4_number_supported(
            number=number,
            text=facts,
            start=match.start(),
            evidence=evidence,
        )
        if accepted:
            if warning and warning not in warnings:
                warnings.append(warning)
            continue
        if token not in unsupported:
            unsupported.append(token)
    return unsupported[:12], warnings[:12]


def _v4_oracle_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    value = evidence.get("oracleContext") or {}
    return value if isinstance(value, dict) else {}


def _controlled_metabolic_abnormality(
    evidence: dict[str, Any],
) -> bool:
    event = (
        evidence.get("controlledEventContext")
        or {}
    )
    metabolic = event.get("metabolic") or {}

    ph = (
        (metabolic.get("ph") or {})
        .get("value")
    )
    lactate = (
        (metabolic.get("lactate") or {})
        .get("value")
    )

    return (
        isinstance(ph, (int, float))
        and ph < 7.35
    ) or (
        isinstance(lactate, (int, float))
        and lactate > 2.2
    )


def _v4_clauses(text: str) -> list[str]:
    """Split prose into local semantic clauses for negation-scope checks.

    The previous validator searched an entire sentence for words such as
    ``absence`` or ``no``. That incorrectly allowed a negative phrase about
    electrolytes to negate chest pain earlier in the same sentence. This
    splitter intentionally creates short local clauses before concept matching.
    """

    output: list[str] = []
    for sentence in _sentences(text):
        parts = re.split(
            r"\s*;\s*|\s*,\s*|\s+\b(?:but|however|whereas|while)\b\s+",
            sentence,
            flags=re.IGNORECASE,
        )
        for part in parts:
            clean = part.strip(" -:\t\r\n")
            if clean:
                output.append(clean)
    return output


def _v4_clause_negates_term(clause: str, term: str) -> bool:
    normalized = _normalize(clause)
    normalized_term = _normalize(term)
    if not normalized_term or normalized_term not in normalized:
        return False

    escaped = re.escape(normalized_term)
    prefix = "|".join(re.escape(item.strip()) for item in V4_LOCAL_NEGATION_PREFIXES)
    suffix = "|".join(re.escape(item.strip()) for item in V4_LOCAL_NEGATION_SUFFIXES)

    before_pattern = rf"\b(?:{prefix})\s*.{{0,36}}\b{escaped}\b"
    after_pattern = rf"\b{escaped}\b.{{0,28}}\b(?:{suffix})\b"
    return bool(
        re.search(before_pattern, normalized, flags=re.IGNORECASE)
        or re.search(after_pattern, normalized, flags=re.IGNORECASE)
    )


def _v4_source_qualified(clause: str) -> bool:
    normalized = _normalize(clause)
    return any(marker in normalized for marker in V4_SOURCE_QUALIFIERS)


def _v4_electrolyte_panel(evidence: dict[str, Any]) -> tuple[list[str], bool, bool | None]:
    context = (evidence.get("controlledEventContext") or {}).get("electrolytes") or {}
    analytes: list[str] = []
    for name in ("potassium", "magnesium", "calcium", "sodium"):
        item = context.get(name) or {}
        if isinstance(item, dict) and item.get("available") is True:
            analytes.append(name)
    complete = len(analytes) == 4
    explicit = context.get("electrolyteAbnormalityPresent")
    return analytes, complete, explicit if isinstance(explicit, bool) else None


def _v4_dedupe_by_category(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.split(":", 1)[0].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _v4_negative_claim_findings(
    facts: str,
    evidence: dict[str, Any],
) -> dict[str, list[str]]:
    """Validate negative statements using local clauses and source ownership.

    Hard errors are reserved for direct contradictions or unsupported patient-
    level absence claims. Source-qualified statements such as "controlled-event
    evidence did not support infection" are allowed. Broad statements based on
    partial evidence are quality issues, which remain displayable with review.
    """

    hard: list[str] = []
    quality: list[str] = []
    warnings: list[str] = []
    internal_conflicts: list[str] = []

    event = evidence.get("controlledEventContext") or {}
    ischemia = event.get("ischemia") or {}
    infection = event.get("infection") or {}
    electrolytes = event.get("electrolytes") or {}
    renal = event.get("renal") or {}
    analytes, electrolyte_complete, electrolyte_negative = _v4_electrolyte_panel(evidence)

    concept_state: dict[str, dict[str, Any]] = {
        "chest pain": {
            "positive": ischemia.get("acuteChestPain") is True,
            "explicitNegative": ischemia.get("acuteChestPain") is False,
            "notSupported": False,
        },
        "ischemic symptoms": {
            "positive": ischemia.get("acuteStemiSupported") is True,
            "explicitNegative": ischemia.get("acuteStemiSupported") is False,
            "notSupported": ischemia.get("acuteStemiSupported") is False,
        },
        "infection": {
            "positive": bool(
                infection.get("infectionSupported") is True
                or infection.get("sepsisSupported") is True
            ),
            "explicitNegative": infection.get("infectionExplicitlyDenied") is True,
            "notSupported": bool(
                infection.get("infectionSupported") is False
                and infection.get("sepsisSupported") is False
            ),
        },
        "electrolyte abnormality": {
            "positive": bool(
                electrolyte_negative is True
                or electrolytes.get("abnormalElectrolytes")
            ),
            "explicitNegative": electrolyte_negative is False,
            "notSupported": electrolyte_negative is False,
        },
        "chronic kidney disease": {
            "positive": renal.get("chronicKidneyDiseaseSupported") is True,
            "explicitNegative": False,
            "notSupported": renal.get("chronicKidneyDiseaseSupported") is False,
        },
        "acute kidney injury": {
            "positive": renal.get("acuteKidneyInjurySupported") is True,
            "explicitNegative": False,
            "notSupported": renal.get("acuteKidneyInjurySupported") is False,
        },
        "renal impairment": {
            "positive": renal.get("renalImpairmentSupported") is True,
            "explicitNegative": False,
            "notSupported": renal.get("renalImpairmentSupported") is False,
        },
        "structural heart disease": {
            "positive": False,
            "explicitNegative": False,
            "notSupported": False,
        },
    }

    for clause in _v4_clauses(facts):
        normalized = _normalize(clause)
        for concept, terms in V4_NEGATIVE_CLAIMS.items():
            matched_terms = [term for term in terms if _normalize(term) in normalized]
            if not matched_terms:
                continue
            if not any(_v4_clause_negates_term(clause, term) for term in matched_terms):
                continue

            state = concept_state.get(concept) or {
                "positive": False,
                "explicitNegative": False,
                "notSupported": False,
            }
            qualified = _v4_source_qualified(clause)

            if state.get("positive"):
                hard.append(
                    f"Response contradicted supplied positive {concept} evidence: {clause}"
                )
                continue

            if concept == "electrolyte abnormality" and state.get("explicitNegative"):
                if electrolyte_complete:
                    continue
                supplied = ", ".join(analytes) or "no analytes"
                quality.append(
                    "Over-broad electrolyte absence claim. The scoped evidence "
                    f"supports only that the supplied {supplied} values did not show "
                    f"an abnormality; use source-qualified wording: {clause}"
                )
                continue

            if state.get("explicitNegative"):
                # An explicit negative flag in the scoped envelope supports the
                # statement, although source qualification remains preferable.
                if not qualified:
                    warnings.append(
                        f"Negative {concept} statement was supported but not source-qualified: {clause}"
                    )
                continue

            if state.get("notSupported"):
                if qualified:
                    continue
                quality.append(
                    f"Source qualification required for {concept}. Say that the "
                    f"controlled-event evidence did not support it rather than claiming "
                    f"patient-level absence: {clause}"
                )
                continue

            if qualified:
                # Missing evidence can support "not established/not supplied" but
                # never a patient-level assertion of absence.
                if any(marker in normalized for marker in ("not supplied", "not established")):
                    continue
                quality.append(
                    f"Ambiguous source-qualified negative statement about {concept}: {clause}"
                )
                continue

            hard.append(
                f"Unsupported negative claim about {concept} at patient level: {clause}"
            )

    return {
        "hardErrors": _v4_dedupe_by_category(hard),
        "qualityErrors": _v4_dedupe_by_category(quality),
        "warnings": _v4_dedupe_by_category(warnings),
        "validatorInternalConflicts": _v4_dedupe_by_category(internal_conflicts),
    }


def _v4_temporal_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    if not _v4_flag("SLM_TEMPORAL_QUALIFICATION_ENABLED", False):
        return []

    output: list[str] = []
    oracle = _v4_oracle_evidence(evidence)
    temporal_items: list[dict[str, Any]] = []
    temporal_items.extend(item for item in oracle.get("labTrends") or [] if isinstance(item, dict))
    temporal_items.extend(item for item in oracle.get("vitalTrends") or [] if isinstance(item, dict))

    for item in temporal_items:
        bucket = _normalize(item.get("temporalBucket"))
        if bucket not in {"historical", "historical_remote"}:
            continue
        label = _normalize(item.get("label") or item.get("field"))
        value = str(item.get("latestValue") if item.get("latestValue") is not None else "")
        for sentence in _sentences(facts):
            normalized = _normalize(sentence)
            if not ((label and label in normalized) or (value and value in normalized)):
                continue
            if any(word in normalized for word in V4_CURRENT_WORDS) and not any(
                qualifier in normalized for qualifier in V4_TEMPORAL_QUALIFIERS
            ):
                output.append(
                    f"Historical Oracle evidence was described as current: {sentence}"
                )
    return list(dict.fromkeys(output))


def _v4_medication_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    if not _v4_flag("SLM_MEDICATION_SEMANTICS_ENABLED", False):
        return []

    output: list[str] = []
    medications = _v4_oracle_evidence(evidence).get("medications") or []
    for item in medications:
        if not isinstance(item, dict):
            continue
        name = _normalize(item.get("name"))
        if not name:
            continue
        aliases = {name}
        base_name = _normalize(str(item.get("name") or "").split("(", 1)[0])
        if base_name:
            aliases.add(base_name)
        first_token = name.split()[0] if name.split() else ""
        if len(first_token) >= 4:
            aliases.add(first_token)
        evidence_type = _normalize(item.get("evidenceType"))
        exposure = item.get("exposureSupported") is True
        causal_allowed = item.get("causalUseAllowed") is True
        for sentence in _sentences(facts):
            normalized = _normalize(sentence)
            if not any(alias and alias in normalized for alias in aliases):
                continue
            if not exposure and any(word in normalized for word in V4_EXPOSURE_WORDS):
                output.append(
                    f"Medication order was presented as exposure or administration: {sentence}"
                )
            if not causal_allowed and any(word in normalized for word in V4_CAUSAL_WORDS):
                output.append(
                    f"Unsupported medication causal claim from {evidence_type or 'record'} evidence: {sentence}"
                )
    return list(dict.fromkeys(output))


def _v4_remote_causal_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    if not _v4_flag("SLM_CAUSAL_VALIDATION_ENABLED", False):
        return []

    output: list[str] = []
    oracle = _v4_oracle_evidence(evidence)
    items: list[tuple[str, str]] = []
    for item in [*(oracle.get("labTrends") or []), *(oracle.get("vitalTrends") or [])]:
        if not isinstance(item, dict):
            continue
        if _normalize(item.get("temporalBucket")) in {"historical", "historical_remote"}:
            label = _normalize(item.get("label") or item.get("field"))
            if label:
                items.append((label, _normalize(item.get("temporalBucket"))))

    for sentence in _sentences(facts):
        normalized = _normalize(sentence)
        if not any(word in normalized for word in V4_CAUSAL_WORDS):
            continue
        for label, bucket in items:
            if label in normalized:
                output.append(
                    f"Unsupported causal claim from {bucket} Oracle evidence: {sentence}"
                )
    return list(dict.fromkeys(output))


def _v4_measurement_conflict_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    output: list[str] = []
    difference_terms = (
        "difference",
        "differs",
        "different",
        "discrepancy",
        "conflict",
        "mismatch",
        "divergence",
        "variation",
        "versus",
        " vs ",
        "compared with",
        "compared to",
        "shorter",
        "longer",
        "higher",
        "lower",
    )
    controlled_source_terms = (
        "controlled event",
        "controlled-event",
        "episode package",
        "episode-package",
        "controlled measurement",
    )
    independent_source_terms = (
        "phase 6",
        "phase6",
        "deterministic waveform",
        "independent measurement",
        "windowed analysis",
    )

    for conflict in evidence.get("measurementConflicts") or []:
        if not isinstance(conflict, dict) or not conflict.get("material"):
            continue

        identifier = str(conflict.get("id") or conflict.get("field") or conflict.get("metric") or "")
        metric_text = _normalize(
            conflict.get("metric")
            or conflict.get("field")
            or conflict.get("measurement")
            or identifier
        )
        if "qrs" in metric_text:
            metric_aliases = ("qrs", "qrs duration", "qrs width")
        elif "qt" in metric_text:
            metric_aliases = ("qt", "qtc", "qt interval", "corrected qt")
        elif "heart" in metric_text or "rate" in metric_text:
            metric_aliases = ("heart rate", "ventricular rate", "rate")
        else:
            words = re.sub(r"[^a-z0-9]+", " ", metric_text).strip()
            metric_aliases = (words,) if words else ()

        controlled = _v601_conflict_number(conflict.get("controlledValue"))
        independent = _v601_conflict_number(
            conflict.get("independentValue")
            if conflict.get("independentValue") is not None
            else conflict.get("phase6Value")
        )

        acknowledged = False
        for clause in _v601_conflict_clauses(facts):
            normalized = _normalize(clause)
            metric_present = any(alias and alias in normalized for alias in metric_aliases)
            difference_present = any(term in normalized for term in difference_terms)
            if not metric_present or not difference_present:
                continue

            controlled_source = any(term in normalized for term in controlled_source_terms)
            independent_source = any(term in normalized for term in independent_source_terms)
            source_comparison = controlled_source and independent_source

            values = _v601_numbers_in_text(clause)
            controlled_present = controlled is not None and _v601_exact_numeric_match(controlled, values)
            independent_present = independent is not None and _v601_exact_numeric_match(independent, values)
            both_values = controlled_present and independent_present

            if both_values or source_comparison:
                acknowledged = True
                break

        if not acknowledged:
            output.append(
                f"Material measurement conflict was not acknowledged: {conflict.get('id')}."
            )
    return output


def _v4_coverage_status(
    facts: str,
    evidence: dict[str, Any],
) -> tuple[dict[str, bool], list[str]]:
    coverage, missing = _coverage_status(facts, evidence)
    normalized = _normalize(facts)
    if "measurementConflict" in coverage:
        coverage["measurementConflict"] = not _v4_measurement_conflict_errors(facts, evidence)
        if coverage["measurementConflict"]:
            missing = [item for item in missing if item != "measurementConflict"]
        elif "measurementConflict" not in missing:
            missing.append("measurementConflict")

    # Explicitly saying that an etiology is not established is valid coverage.
    if "etiologySupport" in coverage and any(
        phrase in normalized
        for phrase in ("not established", "does not establish", "insufficient to establish", "cannot determine")
    ):
        coverage["etiologySupport"] = True
    return coverage, missing


def _v4_correction_evidence(
    evidence: dict[str, Any],
    missing_coverage: list[str],
) -> list[str]:
    contract = evidence.get("validatorContract") or {}
    requirements = {
        str(item.get("id")): item
        for item in contract.get("coverageRequirements") or []
        if isinstance(item, dict)
    }
    output: list[str] = []
    for identifier in missing_coverage:
        requirement = requirements.get(identifier) or {}
        instruction = str(requirement.get("instruction") or "").strip()
        paths = requirement.get("evidencePaths") or []
        if instruction:
            output.append(f"{identifier}: {instruction}")
        for path in paths:
            current: Any = evidence
            for part in str(path).split("."):
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(part)
            if current not in (None, "", [], {}):
                serialized = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
                output.append(f"{path}: {serialized[:1200]}")
    return output[:12]


def _validate_v4_response(
    *,
    response: dict[str, Any],
    diagnostic_event: dict[str, Any],
    supplied_evidence: dict[str, Any],
) -> dict[str, Any]:
    configuration_errors = _v4_configuration_errors(supplied_evidence)
    diagnosis = (supplied_evidence.get("controlledRhythm") or {}).get("diagnosis") or {}
    code = str(diagnosis.get("code") or (diagnostic_event.get("diagnosis") or {}).get("code") or "")
    display = str(diagnosis.get("display") or (diagnostic_event.get("diagnosis") or {}).get("display") or "")

    if configuration_errors:
        return {
            "status": "configuration_error",
            "accepted": False,
            "hardAccepted": False,
            "validatorPassed": False,
            "displayableWithReview": False,
            "qualityReviewRequired": False,
            "retryable": False,
            "reason": configuration_errors[0],
            "configurationErrors": configuration_errors,
            "authoritativeDiagnosisCode": code,
            "authoritativeDiagnosisDisplay": display,
            "errors": configuration_errors,
            "hardErrors": [],
            "qualityErrors": [],
            "warnings": [],
            "contradictions": [],
            "unsupportedFacts": [],
            "evidenceCoverage": {},
            "missingRequiredCoverage": [],
            "evidenceCoverageCount": 0,
            "evidenceCoverageRequired": 0,
            "correctionEvidence": [],
            "recommendedActionsRequired": False,
            "policyVersion": "grounded-response-validator-v4.2",
            "groundingStatus": "configuration_error",
        }

    hard_errors: list[str] = []
    quality_errors: list[str] = []
    warnings: list[str] = []
    contradictions: list[str] = []
    unsupported_facts: list[str] = []

    keys = set(response)
    missing_fields = sorted(V4_ALLOWED_FIELDS - keys)
    extra_fields = sorted(keys - V4_ALLOWED_FIELDS)
    if missing_fields:
        hard_errors.append("Missing output fields: " + ", ".join(missing_fields))
    if extra_fields:
        hard_errors.append("Unsupported output fields: " + ", ".join(extra_fields))

    for field in ("episodeSummary", "detectedEpisodeContext", "mostLikelyEtiology"):
        if not isinstance(response.get(field), str) or not str(response.get(field)).strip():
            hard_errors.append(f"{field} is missing or empty.")
    for field in ("contributingFactors", "uncertaintyAndMissingData"):
        value = response.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            hard_errors.append(f"{field} must be an array of strings.")

    factors = response.get("contributingFactors")
    if isinstance(factors, list) and len(factors) > 5:
        quality_errors.append(
            "contributingFactors contains more than the contract maximum of five items."
        )

    facts = _fact_text(response)
    normalized_facts = _normalize(facts)

    if (
        supplied_evidence.get(
            "clinicalPromptMode"
        )
        == "episode_pack_only"
    ):
        oracle_fact_patterns = (
            r"\bOracle FHIR\b",
            r"\bOracle SMART\b",
            r"\bFHIR\b",
            r"\bOracle medication\b",
            r"\bOracle condition\b",
            r"\bSMART patient\b",
        )

        for pattern in oracle_fact_patterns:
            match = re.search(
                pattern,
                facts,
                flags=re.IGNORECASE,
            )
            if match:
                hard_errors.append(
                    "Oracle FHIR clinical context "
                    "was introduced even though "
                    "episode_pack_only mode excludes it: "
                    + match.group(0)
                )

    phase6_diagnosis_patterns = (
        r"\bindependent ECG analysis confirmed "
        r"ventricular fibrillation\b",
        r"\bPhase 6 confirmed ventricular "
        r"fibrillation\b",
        r"\bdeterministic analysis diagnosed "
        r"ventricular fibrillation\b",
    )

    for pattern in phase6_diagnosis_patterns:
        if re.search(
            pattern,
            facts,
            flags=re.IGNORECASE,
        ):
            quality_errors.append(
                "Phase 6 source-ownership error: "
                "deterministic waveform analysis "
                "supplies measurements and limitations "
                "but does not independently diagnose "
                "the controlled rhythm."
            )
            break

    for phrase in INSTRUCTION_LEAKAGE_PHRASES:
        if phrase in normalized_facts:
            hard_errors.append(f"Prompt instruction leakage detected: '{phrase}'.")

    etiology = _normalize(response.get("mostLikelyEtiology"))
    if etiology in GENERIC_ETIOLOGIES:
        quality_errors.append(
            f"mostLikelyEtiology is too generic: '{response.get('mostLikelyEtiology')}'."
        )

    scoped_event = _v4_scoped_diagnostic_event(supplied_evidence)
    contradictions.extend(_diagnosis_contradictions(facts, scoped_event))
    contradictions.extend(_measurement_contradictions(facts, scoped_event))

    if _controlled_metabolic_abnormality(
        supplied_evidence
    ):
        metabolic_denial_patterns = (
            r"\bno metabolic derangements?\b",
            r"\bno metabolic abnormalit(?:y|ies)\b",
            r"\bno metabolic acidosis\b",
            r"\bno acidosis\b",
            r"\bno lactate elevation\b",
            r"\blactate was normal\b",
        )

        for pattern in metabolic_denial_patterns:
            match = re.search(
                pattern,
                facts,
                flags=re.IGNORECASE,
            )
            if match:
                contradictions.append(
                    "Contradiction with supplied "
                    "controlled-event metabolic evidence: "
                    + match.group(0)
                )

    hard_errors.extend(_treatment_leakage(facts))

    # Watched facts are checked only against the exact scoped envelope.
    normalized_evidence = _normalize(supplied_evidence)
    for term in WATCHED_FACT_TERMS:
        if term in normalized_facts and term not in normalized_evidence and _positive_phrase(facts, (term,)):
            unsupported_facts.append(f"Unsupported case fact introduced: {term}.")

    unsupported_numbers, numeric_warnings = _v4_unsupported_numbers(facts, supplied_evidence)
    warnings.extend(numeric_warnings)
    if unsupported_numbers:
        unsupported_facts.append("Unsupported numeric claims: " + ", ".join(unsupported_numbers))

    negative_findings = _v4_negative_claim_findings(facts, supplied_evidence)
    unsupported_facts.extend(negative_findings.get("hardErrors") or [])
    quality_errors.extend(negative_findings.get("qualityErrors") or [])
    warnings.extend(negative_findings.get("warnings") or [])
    validator_internal_conflicts = list(
        negative_findings.get("validatorInternalConflicts") or []
    )
    unsupported_facts.extend(_v4_medication_errors(facts, supplied_evidence))
    unsupported_facts.extend(_v4_remote_causal_errors(facts, supplied_evidence))
    quality_errors.extend(_v4_temporal_errors(facts, supplied_evidence))
    quality_errors.extend(_v4_measurement_conflict_errors(facts, supplied_evidence))

    coverage, missing_required = _v4_coverage_status(facts, supplied_evidence)
    if missing_required:
        quality_errors.append(
            "Response did not address required evidence categories: "
            + ", ".join(missing_required)
        )

    hard_errors.extend(contradictions)
    hard_errors.extend(unsupported_facts)
    hard_errors = list(dict.fromkeys(hard_errors))
    quality_errors = list(dict.fromkeys(quality_errors))
    warnings = list(dict.fromkeys(warnings))
    contradictions = list(dict.fromkeys(contradictions))
    unsupported_facts = list(dict.fromkeys(unsupported_facts))

    hard_accepted = not hard_errors
    accepted = hard_accepted and not quality_errors
    displayable = hard_accepted and bool(quality_errors)

    # Retry only when the model can materially correct the response. Source-
    # qualification or partial-panel wording is safe to display with review and
    # should not trigger another expensive model request.
    retryable = bool(hard_errors or missing_required)
    correction_evidence = _v4_correction_evidence(supplied_evidence, missing_required)
    if quality_errors and not retryable:
        correction_evidence.extend(
            [
                "Use source-qualified language: 'controlled-event evidence did not support X'.",
                "For partial electrolyte panels, name the supplied analytes instead of saying all electrolytes were normal.",
            ]
        )
        if (
            supplied_evidence.get(
                "clinicalPromptMode"
            )
            != "episode_pack_only"
        ):
            correction_evidence.append(
                "An empty Oracle conditions list means conditions were not returned; it does not prove disease absence."
            )
        correction_evidence = list(dict.fromkeys(correction_evidence))[:12]

    return {
        "status": "accepted" if accepted else "accepted_with_review" if displayable else "rejected",
        "accepted": accepted,
        "hardAccepted": hard_accepted,
        "validatorPassed": hard_accepted,
        "displayableWithReview": displayable,
        "qualityReviewRequired": displayable,
        "retryable": retryable,
        "authoritativeDiagnosisCode": code,
        "authoritativeDiagnosisDisplay": display,
        "errors": [*hard_errors, *quality_errors],
        "hardErrors": hard_errors,
        "qualityErrors": quality_errors,
        "warnings": warnings,
        "contradictions": contradictions,
        "unsupportedFacts": unsupported_facts,
        "validatorInternalConflicts": validator_internal_conflicts,
        "adjudicationEligible": bool(
            not hard_errors
            and (quality_errors or warnings)
        ),
        "evidenceCoverage": coverage,
        "missingRequiredCoverage": missing_required,
        "evidenceCoverageCount": sum(1 for value in coverage.values() if value),
        "evidenceCoverageRequired": sum(
            1
            for requirement in supplied_evidence.get("coverageRequirements") or []
            if isinstance(requirement, dict) and requirement.get("requiredInResponse") is True
        ),
        "correctionEvidence": correction_evidence,
        "recommendedActionsRequired": False,
        "policyVersion": "grounded-response-validator-v4.3",
        "groundingStatus": "accepted" if accepted else "accepted_with_review" if displayable else "rejected",
        "clinicalPromptMode": supplied_evidence.get("clinicalPromptMode"),
        "evidenceFingerprint": supplied_evidence.get("evidenceFingerprint"),
    }


def validate_grounded_response(
    *,
    response: dict[str, Any],
    diagnostic_event: dict[str, Any],
    supplied_evidence: dict[str, Any],
) -> dict[str, Any]:
    review = supplied_evidence.get("evidenceConsistencyReview") or {}
    if isinstance(review, dict) and review.get("status") == "evidence_invalid":
        from app.evaluation_injection.evidence_consistency import (
            evidence_invalid_validation,
        )

        return evidence_invalid_validation(
            review,
            diagnostic_event=diagnostic_event,
        )

    if (
        supplied_evidence.get("schemaVersion") == "slm-evidence-envelope-v4"
        and _v4_flag("SLM_VALIDATOR_V4_ENABLED", False)
    ):
        return _validate_v4_response(
            response=response,
            diagnostic_event=diagnostic_event,
            supplied_evidence=supplied_evidence,
        )

    return _validate_v2_response(
        response=response,
        diagnostic_event=diagnostic_event,
        supplied_evidence=supplied_evidence,
    )

# BEGIN KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
# Final definitions intentionally override earlier implementations. Python
# resolves these names at call time, so existing callers use the corrected
# semantics without creating a second validator implementation.


def _kgen_v6031_phrase_pattern(phrase: str):
    normalized = _normalize(phrase)
    if not normalized:
        return None
    tokens = normalized.replace("-", " ").split()
    separator = r"(?:[\s-]+)"
    return re.compile(
        r"(?<![a-z0-9])"
        + separator.join(re.escape(token) for token in tokens)
        + r"(?![a-z0-9])"
    )


def _kgen_v6031_locally_negated(normalized_sentence: str, match_start: int) -> bool:
    prefix = normalized_sentence[max(0, match_start - 90):match_start]
    return any(marker in prefix for marker in NEGATION_MARKERS)


def _positive_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(text):
        normalized = _normalize(sentence)
        for phrase in phrases:
            pattern = _kgen_v6031_phrase_pattern(phrase)
            if pattern is None:
                continue
            for match in pattern.finditer(normalized):
                prefix = normalized[max(0, match.start() - 8):match.start()]
                # Do not match "sustained VT" inside "non-sustained VT".
                if prefix.endswith("non-") or prefix.endswith("non "):
                    continue
                if _kgen_v6031_locally_negated(normalized, match.start()):
                    continue
                if any(marker in normalized for marker in BASELINE_MARKERS):
                    continue
                return True
    return False


def _supported_numbers(evidence: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", _flatten(evidence)):
        try:
            values.append(float(match.group(0)))
        except ValueError:
            continue
    return values


def _kgen_v6031_numeric_equivalent(output_value: float, evidence_value: float) -> bool:
    difference = abs(float(output_value) - float(evidence_value))
    # Unit-aware checks happen elsewhere; this tolerance handles safe display
    # rounding such as 54 bpm from 54.098 bpm without accepting large changes.
    if difference <= max(0.25, abs(float(evidence_value)) * 0.015):
        return True
    if difference < 1.0 and round(float(output_value)) == round(float(evidence_value)):
        return True
    return False


def _unsupported_numbers(text: str, evidence: dict[str, Any]) -> list[str]:
    supported = _supported_numbers(evidence)
    output: list[str] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", text):
        token = match.group(0)
        number = float(token)
        if abs(number) < 4:
            continue
        if any(_kgen_v6031_numeric_equivalent(number, item) for item in supported):
            continue
        if token not in output:
            output.append(token)
    return output[:12]


def _v4_number_supported(*, number: float, text: str, start: int, evidence: dict[str, Any]):
    supported = _v4_numeric_values(evidence)
    for item in supported:
        if _kgen_v6031_numeric_equivalent(number, item):
            return True, None
    if not _v4_flag("SLM_NUMERIC_TOLERANCE_ENABLED", False):
        return False, None
    window = _normalize(text[max(0, start - 35): start + 35])
    if any(marker in window for marker in ("about", "approximately", "around", "roughly", "nearly", "~")):
        for item in supported:
            if abs(number - item) <= max(1.0, abs(item) * 0.06):
                return True, f"Safe approximate numeric paraphrase: {number:g}."
    return False, None


def _kgen_v6031_conflict_is_etiology_material(conflict: dict[str, Any]) -> bool:
    return any(
        conflict.get(key) is True
        for key in (
            "etiologyMaterial",
            "materialToEtiology",
            "clinicallyMaterialToEtiology",
            "presentationMaterial",
        )
    )


def _v4_measurement_conflict_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    output: list[str] = []
    normalized = _normalize(facts)
    for conflict in evidence.get("measurementConflicts") or []:
        if not isinstance(conflict, dict):
            continue
        if not _kgen_v6031_conflict_is_etiology_material(conflict):
            # Technical/non-etiologic conflicts remain audit metadata and are
            # not required in the user-facing clinical response.
            continue
        controlled = str(
            conflict.get("controlledEventValue", conflict.get("controlledValue", ""))
        )
        independent = str(
            conflict.get("phase6EventValue", conflict.get("independentValue", ""))
        )
        acknowledged = (
            any(
                term in normalized
                for term in ("differs", "difference", "discrepancy", "conflict", "does not match")
            )
            and (controlled in facts or "controlled" in normalized or "episode" in normalized)
            and (independent in facts or "independent" in normalized or "phase 6" in normalized)
        )
        if not acknowledged:
            output.append(
                "Etiologically material measurement conflict was not acknowledged: "
                f"{conflict.get('id')}."
            )
    return output
# END KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
