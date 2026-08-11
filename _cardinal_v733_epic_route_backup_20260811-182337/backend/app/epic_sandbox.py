from __future__ import annotations

import re
from typing import Any


# Epic LaunchPad patients currently exposed to kg-react-epic.
# Deliberately contains names only. The authoritative FHIR Patient ID is
# obtained from Epic's SMART token response at runtime and then verified with
# Patient.Read before a patient is allowed into the evaluation flow.
EPIC_SANDBOX_PATIENTS: dict[str, dict[str, str]] = {
    "cadence_anna": {
        "given": "Anna",
        "family": "Cadence",
        "display": "Cadence, Anna",
    },
    "clin_doc_henry": {
        "given": "Henry",
        "family": "Clin Doc",
        "display": "Clin Doc, Henry",
    },
    "grand_central_john": {
        "given": "John",
        "family": "Grand Central",
        "display": "Grand Central, John",
    },
    "optime_omar": {
        "given": "Omar",
        "family": "Optime",
        "display": "Optime, Omar",
    },
    "nelson_kyle": {
        "given": "Kyle",
        "family": "Nelson",
        "display": "Nelson, Kyle",
    },
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _first_given(name: dict[str, Any]) -> str:
    given = name.get("given") or []
    if isinstance(given, str):
        return given.strip()
    if isinstance(given, list):
        for value in given:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def patient_names(patient: dict[str, Any]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for raw in patient.get("name") or []:
        if not isinstance(raw, dict):
            continue
        family = str(raw.get("family") or "").strip()
        given = _first_given(raw)
        text = str(raw.get("text") or "").strip()
        if given or family or text:
            values.append({"given": given, "family": family, "text": text})
    return values


def patient_display(patient: dict[str, Any]) -> str:
    names = patient_names(patient)
    if not names:
        return "Epic SMART patient"
    name = names[0]
    if name["family"] and name["given"]:
        return f'{name["family"]}, {name["given"]}'
    return name["text"] or " ".join(v for v in [name["given"], name["family"]] if v).strip() or "Epic SMART patient"


def resolve_epic_sandbox_patient(patient: dict[str, Any]) -> dict[str, str] | None:
    """Resolve a verified Epic Patient resource to one of the five LaunchPad patients.

    We compare every FHIR HumanName entry rather than assuming the first entry is
    the official name. No Patient IDs are hard-coded or guessed here.
    """
    for actual in patient_names(patient):
        for key, expected in EPIC_SANDBOX_PATIENTS.items():
            given_match = _norm(actual["given"]) == _norm(expected["given"])
            family_match = _norm(actual["family"]) == _norm(expected["family"])
            display_match = _norm(actual["text"]) in {
                _norm(expected["display"]),
                _norm(f'{expected["given"]} {expected["family"]}'),
            }
            if (given_match and family_match) or display_match:
                return {
                    "patientKey": key,
                    "expectedDisplayName": expected["display"],
                    "actualDisplayName": patient_display(patient),
                    "given": expected["given"],
                    "family": expected["family"],
                }
    return None
