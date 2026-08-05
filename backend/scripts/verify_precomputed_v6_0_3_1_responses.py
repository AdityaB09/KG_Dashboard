from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

PROFILES = (
    "medgemma-27b-it",
    "medgemma-27b-text-it",
    "curated",
)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def load_repository(backend_root: Path):
    path = (
        backend_root
        / "app"
        / "evaluation_injection"
        / "precomputed_response_repository.py"
    )
    spec = importlib.util.spec_from_file_location(
        "kgen_precomputed_response_repository_verify",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load repository module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True)
    parser.add_argument(
        "--evaluated-folder",
        default="medgemma-v6-0-3-1-dual",
    )
    parser.add_argument(
        "--profiles",
        default=",".join(PROFILES),
    )
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()

    backend_root = Path(args.backend_root).resolve()
    output_root = Path(args.output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    profiles = [value.strip() for value in args.profiles.split(",") if value.strip()]
    invalid = sorted(set(profiles) - set(PROFILES))
    if invalid:
        raise ValueError(f"Unsupported profiles: {invalid}")

    os.environ["PRECOMPUTED_SLM_DEMO_ENABLED"] = "true"
    os.environ["PRECOMPUTED_SLM_DEMO_REQUIRED"] = "true"
    os.environ["PRECOMPUTED_SLM_DELAY_SECONDS"] = "0"
    os.environ["PRECOMPUTED_SLM_EVALUATED_FOLDER"] = args.evaluated_folder
    os.environ["PRECOMPUTED_SLM_RUN"] = "2"
    os.environ["PRECOMPUTED_SLM_RESPONSE_SET_ID"] = "medgemma-dual-v6-0-3-1"
    os.environ.pop("PRECOMPUTED_SLM_ROOT", None)
    os.environ.pop("PRECOMPUTED_SLM_MODEL_SLUG", None)
    os.environ.pop("PRECOMPUTED_SLM_MODEL_NAME", None)

    module = load_repository(backend_root)
    reports: list[dict[str, Any]] = []
    for profile in profiles:
        os.environ["PRECOMPUTED_SLM_PROFILE"] = profile
        status = module.precomputed_demo_status()
        report = {
            "profile": profile,
            "status": status,
        }
        reports.append(report)
        write_json(output_root / f"readiness_{profile}.json", report)
        if not status.get("allScenariosReady"):
            raise RuntimeError(
                f"Precomputed profile is not ready: {profile}\n"
                + json.dumps(status, indent=2)
            )
        if status.get("availableCount") != 8 or status.get("missingCount") != 0:
            raise RuntimeError(f"Unexpected scenario readiness for {profile}: {status}")
        if status.get("liveInference") is not False:
            raise RuntimeError(f"Profile incorrectly reports live inference: {profile}")

    summary = {
        "schemaVersion": "kgen-precomputed-verification-report-v2",
        "backendRoot": str(backend_root),
        "evaluatedFolder": args.evaluated_folder,
        "verifiedProfiles": profiles,
        "profileCount": len(profiles),
        "allProfilesReady": True,
        "reports": reports,
    }
    write_json(output_root / "verification_summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
