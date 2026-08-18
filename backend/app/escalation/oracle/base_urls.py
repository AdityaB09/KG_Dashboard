from __future__ import annotations

import os
from urllib.parse import urlparse


def tenant_id_from_fhir_base(fhir_base_url: str | None) -> str:
    text = str(fhir_base_url or "").strip().rstrip("/")
    if not text:
        return ""
    path = urlparse(text).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _legacy_base() -> str:
    return os.getenv("ORACLE_MILLENNIUM_API_BASE_URL", "").strip().rstrip("/")


def _namespace_base(env_name: str, namespace: str, *, fhir_base_url: str | None = None) -> str:
    explicit = os.getenv(env_name, "").strip().rstrip("/")
    if explicit:
        return explicit

    legacy = _legacy_base()
    if legacy:
        # Backward compatibility with the first escalation package. If the old
        # value already ends with a namespace, use it as-is; otherwise append.
        if legacy.rstrip("/").endswith(f"/{namespace}"):
            return legacy
        return f"{legacy}/{namespace}"

    tenant = (
        os.getenv("ORACLE_MILLENNIUM_TENANT_ID", "").strip()
        or os.getenv("ORACLE_MESSAGING_TENANT_ID", "").strip()
    )
    if not tenant:
        tenant = tenant_id_from_fhir_base(
            fhir_base_url or os.getenv("ORACLE_FHIR_BASE_URL", "")
        )
    if not tenant:
        return ""
    host = os.getenv("ORACLE_MESSAGING_API_HOST", "api.cernermillennium.com").strip() or "api.cernermillennium.com"
    return f"https://{host}/{tenant}/{namespace}"


def recipient_api_base(*, fhir_base_url: str | None = None) -> str:
    return _namespace_base(
        "ORACLE_RECIPIENT_API_BASE_URL",
        "recipient",
        fhir_base_url=fhir_base_url,
    )


def message_api_base(*, fhir_base_url: str | None = None) -> str:
    return _namespace_base(
        "ORACLE_MESSAGE_API_BASE_URL",
        "message",
        fhir_base_url=fhir_base_url,
    )


def personnel_api_base(*, fhir_base_url: str | None = None) -> str:
    return _namespace_base(
        "ORACLE_PERSONNEL_API_BASE_URL",
        "personnel",
        fhir_base_url=fhir_base_url,
    )
