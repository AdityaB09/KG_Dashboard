from __future__ import annotations

import time
from typing import Any

from app.config import settings
from app.fhir_http import fhir_get, fhir_post_form


class OracleReauthenticationRequired(RuntimeError):
    """Raised when a new Oracle-backed run requires a fresh SMART launch."""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _resolve_token_endpoint(
    token_state: dict[str, Any],
) -> str:
    cached = str(token_state.get("token_endpoint") or "").strip()
    if cached:
        return cached

    fhir_base_url = str(
        token_state.get("fhir_base_url") or ""
    ).strip().rstrip("/")

    if not fhir_base_url:
        raise OracleReauthenticationRequired(
            "Oracle SMART token state has no FHIR base URL."
        )

    configuration = await fhir_get(
        fhir_base_url,
        "/.well-known/smart-configuration",
        timeout=20,
    )
    endpoint = str(
        configuration.get("token_endpoint") or ""
    ).strip()

    if not endpoint:
        raise OracleReauthenticationRequired(
            "Oracle SMART configuration has no token endpoint."
        )

    token_state["token_endpoint"] = endpoint
    return endpoint


async def ensure_fresh_oracle_token(
    token_state: dict[str, Any],
    *,
    min_validity_seconds: int = 90,
    force: bool = False,
) -> dict[str, Any]:
    """
    Refresh the Oracle access token in place when it is expired or close to expiry.

    The returned dictionary contains refresh metadata only. It never includes
    access_token or refresh_token values.
    """
    now = time.time()
    expires_at = _as_float(
        token_state.get("expires_at_epoch"),
        0.0,
    )
    access_token = str(
        token_state.get("access_token") or ""
    ).strip()

    if (
        not force
        and access_token
        and expires_at > now + max(0, min_validity_seconds)
    ):
        return {
            "ready": True,
            "refreshed": False,
            "expiresAtEpoch": expires_at,
        }

    refresh_token = str(
        token_state.get("refresh_token") or ""
    ).strip()

    if not refresh_token:
        raise OracleReauthenticationRequired(
            "The Oracle SMART access token expired and no refresh token "
            "is available. Complete a new Oracle SMART launch."
        )

    endpoint = await _resolve_token_endpoint(token_state)

    refreshed = await fhir_post_form(
        endpoint,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.ORACLE_CLIENT_ID,
        },
        timeout=30,
    )

    new_access_token = str(
        refreshed.get("access_token") or ""
    ).strip()

    if not new_access_token:
        raise OracleReauthenticationRequired(
            "Oracle did not return a new access token. "
            "Complete a new Oracle SMART launch."
        )

    expires_in = int(
        _as_float(
            refreshed.get("expires_in"),
            570.0,
        )
    )

    token_state["access_token"] = new_access_token
    token_state["expires_at_epoch"] = now + max(60, expires_in)
    token_state["scope"] = (
        refreshed.get("scope")
        or token_state.get("scope")
    )
    token_state["refresh_token"] = (
        refreshed.get("refresh_token")
        or token_state.get("refresh_token")
    )
    token_state["patient_id"] = (
        refreshed.get("patient")
        or token_state.get("patient_id")
    )
    token_state["encounter_id"] = (
        refreshed.get("encounter")
        or token_state.get("encounter_id")
    )
    token_state["_last_refresh_at_epoch"] = now

    return {
        "ready": True,
        "refreshed": True,
        "expiresAtEpoch": token_state["expires_at_epoch"],
    }
