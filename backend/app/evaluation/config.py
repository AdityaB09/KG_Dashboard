from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

# Load backend/.env for both:
# 1. FastAPI started through main.py
# 2. Standalone CLI:
#    python -m app.evaluation.cli ...
#
# Existing shell/Render variables still win because override=False.
load_dotenv(
    _BACKEND_ROOT / ".env",
    override=False,
)


def env_bool(
    name: str,
    default: bool = False,
) -> bool:
    fallback = (
        "true"
        if default
        else "false"
    )

    return (
        os.getenv(
            name,
            fallback,
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def backend_root() -> Path:
    return _BACKEND_ROOT


def dataset_root() -> Path:
    configured = os.getenv(
        "SLM_EVAL_DATASET_ROOT",
        "SLM_Eval",
    ).strip()

    path = Path(configured)

    if not path.is_absolute():
        path = (
            backend_root()
            / path
        )

    return path.resolve()


def results_root() -> Path:
    configured = os.getenv(
        "SLM_EVAL_RESULTS_PATH",
        "data/evaluation_runs",
    ).strip()

    path = Path(configured)

    if not path.is_absolute():
        path = (
            backend_root()
            / path
        )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path.resolve()


def evaluation_enabled() -> bool:
    return env_bool(
        "ENABLE_SLM_EVAL",
        False,
    )


def model_evaluation_allowed() -> bool:
    return env_bool(
        "SLM_EVAL_ALLOW_MODEL",
        False,
    )


def slm_base_url() -> str:
    return (
        os.getenv(
            "SLM_BASE_URL",
            "http://127.0.0.1:11434/v1",
        )
        .strip()
        .rstrip("/")
    )


def slm_chat_path() -> str:
    path = os.getenv(
        "SLM_CHAT_PATH",
        "/chat/completions",
    ).strip()

    return (
        path
        if path.startswith("/")
        else f"/{path}"
    )


def slm_model() -> str:
    return os.getenv(
        "SLM_MODEL",
        "",
    ).strip()


def slm_api_key() -> str:
    return os.getenv(
        "SLM_API_KEY",
        "",
    ).strip()


def slm_timeout_seconds() -> float:
    return float(
        os.getenv(
            "SLM_TIMEOUT_SECONDS",
            "180",
        )
    )


def slm_max_output_tokens() -> int:
    return int(
        os.getenv(
            "SLM_MAX_OUTPUT_TOKENS",
            "1200",
        )
    )

def slm_omit_sampling_params() -> bool:
    return env_bool(
        "SLM_OMIT_SAMPLING_PARAMS",
        False,
    )


def slm_reasoning_effort() -> str:
    value = os.getenv(
        "SLM_REASONING_EFFORT",
        "",
    ).strip().lower()
    return value if value in {"low", "medium", "high"} else ""

