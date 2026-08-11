from __future__ import annotations

import json
from pathlib import Path

from app.evaluation_demo.mapping import resolve_patient_plan
from app.evaluation_injection.etiology_v7 import (
    V7_RESPONSE_FIELDS,
    load_scenario_record,
    sanitize_episode_for_v7,
    validate_v7_response,
)
from app.evaluation_injection.scenario_catalog import SCENARIO_ORDER


ALL_14 = (
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
    "WCT-DIFF-009",
    "WPW-AFIB-010",
    "FLUTTER-IC-011",
    "PERI-STEMI-012",
    "BRASH-013",
    "AMIO-DDI-014",
)


def test_catalog_contains_all_14_v7_scenarios():
    assert SCENARIO_ORDER == ALL_14
    for scenario_id in ALL_14:
        record = load_scenario_record(scenario_id)
        assert record["episodeId"] == scenario_id
        assert len(record["ecg"]["waveform"]["II"]) > 0


def test_v7_model_input_removes_raw_waveform_and_upstream_rhythm_label():
    prepared = sanitize_episode_for_v7(load_scenario_record("WPW-AFIB-010"))

    assert "waveform" not in prepared["ecg"]
    assert "rhythm" not in prepared["ecg"]["measurements"]
    assert "type" not in prepared["episode"]
    assert "display" not in prepared["episode"]
    assert "preEventNote" in prepared["ecg"]["measurements"]
    assert "stDeviationMm" in prepared["ecg"]["measurements"]


def test_bundled_medgemma_v7_responses_are_contract_valid():
    backend_root = Path(__file__).resolve().parents[2]
    response_root = (
        backend_root
        / "data"
        / "etiology_v7_precomputed"
        / "google-medgemma-27b-it"
    )

    for scenario_id in ALL_14:
        payload = json.loads(
            (response_root / f"{scenario_id}.json").read_text(encoding="utf-8")
        )
        assert payload["generationSucceeded"] is True
        assert payload["validContract"] is True
        response = validate_v7_response(payload["modelResponse"])
        assert set(response) == set(V7_RESPONSE_FIELDS)


def test_oracle_multi_scenario_selection_is_stable_for_same_smart_session(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "EVALUATION_INJECTION_ALLOWED_SCENARIOS",
        list(ALL_14),
    )

    first = resolve_patient_plan(
        patient_id="12724065",
        patient_display=None,
        selection_key="same-smart-session",
    )
    second = resolve_patient_plan(
        patient_id="12724065",
        patient_display=None,
        selection_key="same-smart-session",
    )

    assert first["scenarioId"] == second["scenarioId"]
    assert first["selectionMode"] == "stable_random_per_smart_session"
    assert first["scenarioCandidates"] == ["VFIB-STEMI-001", "PERI-STEMI-012"]
