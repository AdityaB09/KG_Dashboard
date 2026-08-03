from __future__ import annotations

import json

from app.evaluation_injection.answer_key_loader import (
    list_answer_key_scenarios,
    load_scenario_answer_key,
)
from app.evaluation_injection.canonical_episode_repository import (
    list_canonical_scenarios,
)
from app.evaluation_injection.model_registry import list_models


EXPECTED_SCENARIOS = [
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
]


def main() -> None:
    key_scenarios = list_answer_key_scenarios()
    canonical_scenarios = list_canonical_scenarios()
    models = list_models(enabled_only=False)

    answer_key_status = {}
    for scenario_id in EXPECTED_SCENARIOS:
        try:
            load_scenario_answer_key(scenario_id, allow_legacy_fallback=False)
            answer_key_status[scenario_id] = "ready"
        except Exception as exc:
            answer_key_status[scenario_id] = f"error: {exc}"

    report = {
        "schemaVersion": "universal-grounded-setup-status-v1",
        "answerKeys": answer_key_status,
        "answerKeyScenarioCount": len(key_scenarios),
        "canonicalScenarios": canonical_scenarios,
        "canonicalScenarioCount": len(canonical_scenarios),
        "missingCanonicalScenarios": [
            scenario_id
            for scenario_id in EXPECTED_SCENARIOS
            if scenario_id not in canonical_scenarios
        ],
        "models": models,
        "enabledModelCount": sum(1 for model in models if model.get("enabled", True)),
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
