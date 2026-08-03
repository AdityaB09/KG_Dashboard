from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_STATUSES = {
    "VFIB-STEMI-001": "accepted",
    "TORSADES-LQT-002": "accepted",
    "VT-ISCHEMIC-003": "accepted",
    "AFIB-RVR-SEPSIS-004": "accepted_with_review",
    "CHB-HYPERK-005": "accepted",
    "BRADY-DIGTOX-006": "accepted",
    "SVT-PSVT-007": "accepted",
    "NSVT-ECTOPY-008": "accepted_with_review",
}

FINGERPRINT_KEYS = {
    "fingerprint",
    "promptFingerprint",
    "sourcePromptFingerprint",
    "groundedInputFingerprint",
    "evidenceFingerprint",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _report_path(root: Path) -> Path:
    if root.is_file():
        return root
    direct = root / "model_selection_report.json"
    if direct.is_file():
        return direct
    matches = sorted(root.rglob("model_selection_report.json"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one model_selection_report.json under {root}, found {len(matches)}")
    return matches[0]


def _runs(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(run.get("scenarioId")): run
        for run in report.get("runs") or []
        if run.get("scenarioId")
    }


def _resolve_run_dir(report_root: Path, run: dict[str, Any]) -> Path | None:
    raw = run.get("outputDirectory")
    if raw:
        candidate = Path(str(raw))
        if candidate.is_dir():
            return candidate
    scenario = str(run.get("scenarioId") or "")
    candidates = sorted(
        path
        for path in report_root.rglob("run-*")
        if path.is_dir() and scenario in path.parts
    )
    return candidates[0] if len(candidates) == 1 else None


def _canonical_json_hash(path: Path) -> str:
    value = _load(path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _response_file(directory: Path | None) -> Path | None:
    if directory is None:
        return None
    preferred = directory / "cardinal_model_response.json"
    if preferred.is_file():
        return preferred
    matches = sorted(directory.glob("cardinal_model_response*.json"))
    return matches[0] if matches else None


def _collect_fingerprints(value: Any, output: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FINGERPRINT_KEYS and isinstance(item, str) and item:
                output.setdefault(key, set()).add(item)
            _collect_fingerprints(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_fingerprints(item, output)


def _fingerprints(directory: Path | None) -> dict[str, list[str]]:
    if directory is None:
        return {}
    output: dict[str, set[str]] = {}
    names = (
        "grounded_model_input.json",
        "grounded_model_messages.json",
        "prompt_package.json",
        "metadata.json",
        "run_summary.json",
        "cardinal_model_response.json",
    )
    for name in names:
        path = directory / name
        if not path.is_file():
            continue
        try:
            _collect_fingerprints(_load(path), output)
        except (OSError, json.JSONDecodeError):
            continue
    return {key: sorted(values) for key, values in sorted(output.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify exact eight-response V6.0.1 local revalidation.")
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-missing-fingerprints", action="store_true")
    args = parser.parse_args()

    before_report_path = _report_path(args.before.resolve())
    after_report_path = _report_path(args.after.resolve())
    before_report = _load(before_report_path)
    after_report = _load(after_report_path)
    before_runs = _runs(before_report)
    after_runs = _runs(after_report)

    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    if set(after_runs) != set(EXPECTED_STATUSES):
        failures.append(
            f"After report scenario set mismatch: expected {sorted(EXPECTED_STATUSES)}, got {sorted(after_runs)}"
        )

    for scenario, expected_status in EXPECTED_STATUSES.items():
        previous = before_runs.get(scenario, {})
        current = after_runs.get(scenario, {})
        before_dir = _resolve_run_dir(before_report_path.parent, previous)
        after_dir = _resolve_run_dir(after_report_path.parent, current)
        before_response = _response_file(before_dir)
        after_response = _response_file(after_dir)

        response_unchanged: bool | None = None
        before_response_hash = None
        after_response_hash = None
        if before_response and after_response:
            before_response_hash = _canonical_json_hash(before_response)
            after_response_hash = _canonical_json_hash(after_response)
            response_unchanged = before_response_hash == after_response_hash
            if not response_unchanged:
                failures.append(f"{scenario}: model response content changed")
        else:
            failures.append(f"{scenario}: could not locate both before and after model-response files")

        before_fingerprints = _fingerprints(before_dir)
        after_fingerprints = _fingerprints(after_dir)
        fingerprints_unchanged = bool(before_fingerprints) and before_fingerprints == after_fingerprints
        if not fingerprints_unchanged and not args.allow_missing_fingerprints:
            failures.append(
                f"{scenario}: source/prompt fingerprints are missing or changed; before={before_fingerprints}, after={after_fingerprints}"
            )

        actual_status = current.get("status")
        if actual_status != expected_status:
            failures.append(f"{scenario}: expected {expected_status}, got {actual_status}")
        if current.get("validatorPassed") is not True:
            failures.append(f"{scenario}: validatorPassed is not true")
        if current.get("safetyPass") is not True:
            failures.append(f"{scenario}: safetyPass is not true")

        rows.append(
            {
                "scenario": scenario,
                "expectedStatus": expected_status,
                "actualStatus": actual_status,
                "validatorPassed": current.get("validatorPassed"),
                "safetyPass": current.get("safetyPass"),
                "responseUnchanged": response_unchanged,
                "beforeResponseSha256": before_response_hash,
                "afterResponseSha256": after_response_hash,
                "fingerprintsUnchanged": fingerprints_unchanged,
                "beforeFingerprints": before_fingerprints,
                "afterFingerprints": after_fingerprints,
                "scoreBefore": previous.get("totalScore"),
                "scoreAfter": current.get("totalScore"),
            }
        )

    payload = {
        "schemaVersion": "kgen-v6-0-1-revalidation-verification-v1",
        "repairName": "KGEN V6.0.1 Validator Semantics Repair",
        "passed": not failures,
        "failureCount": len(failures),
        "failures": failures,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
