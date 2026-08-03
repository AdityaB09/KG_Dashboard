from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that one evaluation episode used the complete episode "
            "package plus Phase 6 and excluded Oracle FHIR clinical context."
        )
    )
    parser.add_argument(
        "--episode-dir",
        required=True,
    )
    args = parser.parse_args()

    episode_dir = Path(args.episode_dir).resolve()
    evidence_path = episode_dir / "slm_evidence_v4.json"
    messages_path = (
        episode_dir
        / "grounded_model_messages.attempt-1.json"
    )

    failures: list[str] = []

    if not evidence_path.exists():
        failures.append(
            f"Missing evidence artifact: {evidence_path}"
        )
        evidence = {}
    else:
        evidence = _load(evidence_path)

    if (
        evidence.get("clinicalPromptMode")
        != "episode_pack_only"
    ):
        failures.append(
            "clinicalPromptMode is not episode_pack_only."
        )

    oracle = evidence.get("oracleContext") or {}
    if oracle.get("available") is not False:
        failures.append(
            "oracleContext.available is not false."
        )
    if oracle.get("excludedByPolicy") is not True:
        failures.append(
            "oracleContext.excludedByPolicy is not true."
        )

    manifest = evidence.get("sourceManifest") or {}
    if (
        manifest.get("patientContextSource")
        != "complete_episode_pack"
    ):
        failures.append(
            "Patient context source is not complete_episode_pack."
        )
    if (
        manifest.get("oracleFhirClinicalContextUsed")
        is not False
    ):
        failures.append(
            "Oracle FHIR clinical context is not explicitly excluded."
        )

    pack = evidence.get("episodePackContext") or {}
    if not pack.get("patient"):
        failures.append(
            "Episode-pack patient is missing."
        )
    if not pack.get("labs"):
        failures.append(
            "Episode-pack labs are missing."
        )
    if not pack.get("clinicalContext"):
        failures.append(
            "Episode-pack clinical context is missing."
        )

    phase6 = evidence.get("deterministicAnalysis") or {}
    if not phase6:
        failures.append(
            "Phase 6 deterministic analysis is missing."
        )

    if messages_path.exists():
        request = _load(messages_path)
        text = json.dumps(
            request,
            ensure_ascii=False,
        ).lower()
        forbidden = [
            marker
            for marker in (
                "oracle",
                "fhir",
                "smart, wilma",
                "oracle_smart_fhir",
            )
            if marker in text
        ]
        if forbidden:
            failures.append(
                "Model request contains forbidden Oracle/FHIR clinical "
                "content: "
                + ", ".join(forbidden)
            )
    else:
        failures.append(
            f"Missing model request artifact: {messages_path}"
        )

    if failures:
        print("EPISODE-PACK-ONLY CHECK: FAIL")
        for item in failures:
            print(f"- {item}")
        return 1

    print("EPISODE-PACK-ONLY CHECK: PASS")
    print(
        json.dumps(
            {
                "episodeDirectory": str(episode_dir),
                "clinicalPromptMode": (
                    evidence.get("clinicalPromptMode")
                ),
                "patientContextSource": (
                    manifest.get("patientContextSource")
                ),
                "oracleFhirClinicalContextUsed": (
                    manifest.get(
                        "oracleFhirClinicalContextUsed"
                    )
                ),
                "phase6Included": True,
                "episodePackPatient": (
                    pack.get("patient")
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
