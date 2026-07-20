from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(
            os.getenv(
                name,
                str(default),
            )
        )
    except ValueError:
        value = default

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def _float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(
            os.getenv(
                name,
                str(default),
            )
        )
    except ValueError:
        value = default

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


@dataclass(frozen=True)
class Phase7Settings:
    schema_version: str = "phase-7-v1"

    enabled: bool = _bool(
        "PHASE7_ENABLED",
        True,
    )

    auto_run_after_capture: bool = _bool(
        "PHASE7_AUTO_RUN_AFTER_CAPTURE",
        True,
    )

    debounce_seconds: float = _float(
        "PHASE7_DEBOUNCE_SECONDS",
        2.0,
        0.0,
        30.0,
    )

    load_clinical_context: bool = _bool(
        "PHASE7_LOAD_CLINICAL_CONTEXT",
        True,
    )

    allow_latest_oracle_session: bool = _bool(
        "PHASE7_ALLOW_LATEST_ORACLE_SESSION",
        True,
    )

    run_slm_automatically: bool = _bool(
        "PHASE7_RUN_SLM_AUTOMATICALLY",
        False,
    )

    maximum_episode_summaries: int = _int(
        "PHASE7_MAX_EPISODE_SUMMARIES",
        8,
        1,
        25,
    )

    maximum_lab_trends: int = _int(
        "PHASE7_MAX_LAB_TRENDS",
        12,
        1,
        50,
    )

    maximum_vital_trends: int = _int(
        "PHASE7_MAX_VITAL_TRENDS",
        12,
        1,
        50,
    )

    maximum_medications: int = _int(
        "PHASE7_MAX_MEDICATIONS",
        20,
        1,
        100,
    )

    maximum_conditions: int = _int(
        "PHASE7_MAX_CONDITIONS",
        20,
        1,
        100,
    )

    maximum_encounters: int = _int(
        "PHASE7_MAX_ENCOUNTERS",
        12,
        1,
        50,
    )

    maximum_reports: int = _int(
        "PHASE7_MAX_REPORTS",
        12,
        1,
        50,
    )

    maximum_documents: int = _int(
        "PHASE7_MAX_DOCUMENTS",
        12,
        1,
        50,
    )

    maximum_prompt_characters: int = _int(
        "PHASE7_MAX_PROMPT_CHARACTERS",
        30000,
        5000,
        100000,
    )

    slm_enabled: bool = _bool(
        "SLM_ENABLED",
        False,
    )

    slm_base_url: str = os.getenv(
        "SLM_BASE_URL",
        "",
    ).strip()

    slm_chat_path: str = os.getenv(
        "SLM_CHAT_PATH",
        "/chat/completions",
    ).strip()

    slm_model: str = os.getenv(
        "SLM_MODEL",
        "",
    ).strip()

    slm_api_key: str = os.getenv(
        "SLM_API_KEY",
        "",
    ).strip()

    slm_timeout_seconds: float = _float(
        "SLM_TIMEOUT_SECONDS",
        90.0,
        5.0,
        300.0,
    )

    slm_max_output_tokens: int = _int(
        "SLM_MAX_OUTPUT_TOKENS",
        1200,
        128,
        8192,
    )


phase7_settings = Phase7Settings()
