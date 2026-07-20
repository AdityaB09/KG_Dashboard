from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from app.clinical_context import (
    clinical_context_service,
)
from app.config import settings

from app.phase7.config import (
    phase7_settings,
)


@dataclass(frozen=True)
class OracleContextCredentials:
    patient_id: str | None
    access_token: str | None
    fhir_base_url: str | None
    source: str
    session_count: int
    limitation: str | None = None


def _valid_session(
    value: Mapping[str, Any],
) -> bool:
    access_token = value.get(
        "access_token"
    )

    if not access_token:
        return False

    expires_at = value.get(
        "expires_at_epoch"
    )

    if expires_at is None:
        return True

    try:
        return float(
            expires_at
        ) > time.time() + 15.0

    except (
        TypeError,
        ValueError,
    ):
        return False


def _session_created_at(
    value: Mapping[str, Any],
) -> float:
    try:
        return float(
            value.get(
                "created_at_epoch"
            )
            or 0.0
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def resolve_oracle_credentials(
    *,
    token_override: Mapping[
        str,
        Any,
    ]
    | None = None,
    requested_patient_id: str | None = None,
) -> OracleContextCredentials:
    if (
        token_override
        and _valid_session(
            token_override
        )
    ):
        return OracleContextCredentials(
            patient_id=(
                requested_patient_id
                or token_override.get(
                    "patient_id"
                )
                or settings
                .ORACLE_TEST_PATIENT_ID
                or None
            ),
            access_token=(
                token_override.get(
                    "access_token"
                )
            ),
            fhir_base_url=(
                token_override.get(
                    "fhir_base_url"
                )
                or settings
                .ORACLE_FHIR_BASE_URL
                or None
            ),
            source=(
                "request_oracle_smart_session"
            ),
            session_count=1,
        )

    try:
        from app.oracle_smart import (
            SMART_TOKEN_STORE,
        )
    except ImportError:
        SMART_TOKEN_STORE = {}

    valid = [
        dict(value)
        for value in (
            SMART_TOKEN_STORE.values()
        )
        if _valid_session(value)
    ]

    if requested_patient_id:
        matching = [
            value
            for value in valid
            if str(
                value.get("patient_id")
                or ""
            )
            == str(
                requested_patient_id
            )
        ]

        if matching:
            selected = max(
                matching,
                key=_session_created_at,
            )

            return OracleContextCredentials(
                patient_id=(
                    requested_patient_id
                ),
                access_token=(
                    selected.get(
                        "access_token"
                    )
                ),
                fhir_base_url=(
                    selected.get(
                        "fhir_base_url"
                    )
                    or settings
                    .ORACLE_FHIR_BASE_URL
                    or None
                ),
                source=(
                    "matching_oracle_smart_session"
                ),
                session_count=len(valid),
            )

    if (
        valid
        and phase7_settings
        .allow_latest_oracle_session
    ):
        selected = max(
            valid,
            key=_session_created_at,
        )

        limitation = None

        if len(valid) > 1:
            limitation = (
                "Multiple Oracle SMART sessions "
                "were active. Research automation "
                "used the newest valid session. "
                "Production systems must bind an "
                "incident to an authenticated user "
                "and patient explicitly."
            )

        return OracleContextCredentials(
            patient_id=(
                requested_patient_id
                or selected.get(
                    "patient_id"
                )
                or settings
                .ORACLE_TEST_PATIENT_ID
                or None
            ),
            access_token=(
                selected.get(
                    "access_token"
                )
            ),
            fhir_base_url=(
                selected.get(
                    "fhir_base_url"
                )
                or settings
                .ORACLE_FHIR_BASE_URL
                or None
            ),
            source=(
                "latest_valid_oracle_smart_session"
            ),
            session_count=len(valid),
            limitation=limitation,
        )

    return OracleContextCredentials(
        patient_id=(
            requested_patient_id
            or settings
            .ORACLE_TEST_PATIENT_ID
            or None
        ),
        access_token=None,
        fhir_base_url=(
            settings
            .ORACLE_FHIR_BASE_URL
            or None
        ),
        source=(
            "configured_test_patient"
            if (
                requested_patient_id
                or settings
                .ORACLE_TEST_PATIENT_ID
            )
            else "no_oracle_context"
        ),
        session_count=len(valid),
        limitation=(
            "No unexpired Oracle SMART session "
            "was available to the automatic "
            "background pipeline."
        ),
    )


async def load_or_reuse_clinical_context(
    *,
    incident_id: str,
    force: bool,
    token_override: Mapping[
        str,
        Any,
    ]
    | None = None,
    requested_patient_id: str | None = None,
) -> tuple[
    dict[str, Any],
    OracleContextCredentials,
]:
    current = (
        clinical_context_service
        .get(incident_id)
    )

    if (
        not force
        and current.get("status")
        in {
            "ready",
            "partial",
        }
    ):
        credentials = (
            resolve_oracle_credentials(
                token_override=(
                    token_override
                ),
                requested_patient_id=(
                    requested_patient_id
                ),
            )
        )

        return (
            dict(current),
            credentials,
        )

    credentials = (
        resolve_oracle_credentials(
            token_override=(
                token_override
            ),
            requested_patient_id=(
                requested_patient_id
            ),
        )
    )

    from app.fhir_cache.service import (
    fhir_cache_service,
)

    context = await fhir_cache_service.get_or_load(
        incident_id=incident_id,
        patient_id=credentials.patient_id,
        access_token=credentials.access_token,
        fhir_base_url=(
            credentials.fhir_base_url
            or settings.ORACLE_FHIR_BASE_URL
        ),
        force_refresh=force,
    )

    return (
        dict(context),
        credentials,
    )
