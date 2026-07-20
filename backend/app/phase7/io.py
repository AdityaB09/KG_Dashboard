from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


_SAFE_ID = re.compile(
    r"[^A-Za-z0-9_.-]+"
)


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_identifier(
    value: str,
) -> str:
    cleaned = _SAFE_ID.sub(
        "-",
        str(value),
    ).strip("-")

    if not cleaned:
        raise ValueError(
            "A nonempty identifier is required."
        )

    return cleaned


def storage_directory(
    incident_id: str,
) -> Path:
    root = Path(
        settings.INCIDENT_STORAGE_PATH
    )

    path = (
        root
        / "phase7"
        / safe_identifier(
            incident_id
        )
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def status_path(
    incident_id: str,
) -> Path:
    return (
        storage_directory(
            incident_id
        )
        / "status.json"
    )


def evidence_path(
    incident_id: str,
) -> Path:
    return (
        storage_directory(
            incident_id
        )
        / "evidence_package.json"
    )


def prompt_path(
    incident_id: str,
) -> Path:
    return (
        storage_directory(
            incident_id
        )
        / "prompt_package.json"
    )


def slm_response_path(
    incident_id: str,
) -> Path:
    return (
        storage_directory(
            incident_id
        )
        / "slm_response.json"
    )


def write_json_atomic(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        f"{path.suffix}.tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def read_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            str(path)
        )

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )
