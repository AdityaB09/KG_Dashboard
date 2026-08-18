from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


class OracleSystemAuthError(RuntimeError):
    """Safe exception for Oracle Millennium System-app authentication failures."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        response_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.response_excerpt = response_excerpt


@dataclass
class _CachedToken:
    access_token: str
    token_type: str
    expires_at_epoch: float
    scope: str


_cache: _CachedToken | None = None
_lock = asyncio.Lock()


def _value(name: str) -> str:
    return os.getenv(name, "").strip()


def _scopes() -> list[str]:
    return [item for item in _value("ORACLE_MESSAGING_SYSTEM_SCOPES").split() if item]


def system_token_url() -> str:
    explicit = _value("ORACLE_MESSAGING_TOKEN_URL")
    if explicit:
        return explicit.rstrip("/")

    tenant = _value("ORACLE_MESSAGING_TENANT_ID") or _value("ORACLE_MILLENNIUM_TENANT_ID")
    if not tenant:
        return ""

    auth_base = _value("ORACLE_MESSAGING_AUTH_BASE_URL") or "https://authorization.cerner.com"
    host = _value("ORACLE_MESSAGING_API_HOST") or "api.cernermillennium.com"
    return (
        f"{auth_base.rstrip('/')}/tenants/{tenant}/hosts/{host}/"
        "protocols/oauth2/profiles/smart-v1/token"
    )


def system_auth_readiness() -> dict[str, Any]:
    client_id = _value("ORACLE_MESSAGING_SYSTEM_CLIENT_ID")
    client_secret = _value("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET")
    tenant = _value("ORACLE_MESSAGING_TENANT_ID") or _value("ORACLE_MILLENNIUM_TENANT_ID")
    scopes = _scopes()
    required = {
        "oraclehealth:millennium:recipient",
        "oraclehealth:millennium:message",
    }
    missing_scopes = sorted(required.difference(scopes))
    missing_values = [
        label
        for label, value in (
            ("ORACLE_MESSAGING_SYSTEM_CLIENT_ID", client_id),
            ("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET", client_secret),
            ("ORACLE_MESSAGING_TENANT_ID", tenant),
        )
        if not value
    ]
    url = system_token_url()
    if not url:
        missing_values.append("ORACLE_MESSAGING_TOKEN_URL/tenant")

    configured = not missing_values and not missing_scopes
    cached = _cache is not None and _cache.expires_at_epoch > time.time()
    return {
        "state": "READY" if configured else "MISCONFIGURED",
        "configured": configured,
        "clientIdConfigured": bool(client_id),
        "clientSecretConfigured": bool(client_secret),
        "tenantIdConfigured": bool(tenant),
        "tokenUrl": url or None,
        "apiHost": _value("ORACLE_MESSAGING_API_HOST") or "api.cernermillennium.com",
        "requestedScopes": scopes,
        "requiredScopesPresent": not missing_scopes,
        "missingScopes": missing_scopes,
        "missingValues": missing_values,
        "personnelScopePresent": "oraclehealth:millennium:personnel" in scopes,
        "cachedTokenAvailable": cached,
        "legacyStaticBearerConfigured": bool(_value("ORACLE_MILLENNIUM_BEARER_TOKEN")),
    }


def _safe_response_excerpt(response: httpx.Response) -> str:
    # Never return request headers or credentials. Oracle error bodies are useful
    # for setup, but keep them bounded.
    return (response.text or "")[:1200]


async def _request_system_token() -> tuple[str, dict[str, Any]]:
    readiness = system_auth_readiness()
    if not readiness["configured"]:
        raise OracleSystemAuthError(
            "Oracle Millennium System messaging credentials are incomplete."
        )

    client_id = _value("ORACLE_MESSAGING_SYSTEM_CLIENT_ID")
    client_secret = _value("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET")
    token_url = str(readiness["tokenUrl"])
    scopes = _scopes()
    timeout = float(_value("ORACLE_MESSAGING_TOKEN_TIMEOUT_SECONDS") or "20")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            token_url,
            auth=(client_id, client_secret),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
            data={
                "grant_type": "client_credentials",
                "scope": " ".join(scopes),
            },
        )

    if response.status_code >= 400:
        raise OracleSystemAuthError(
            "Oracle Millennium System token request was rejected.",
            http_status=response.status_code,
            response_excerpt=_safe_response_excerpt(response),
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OracleSystemAuthError(
            "Oracle Millennium System token response was not JSON.",
            http_status=response.status_code,
            response_excerpt=_safe_response_excerpt(response),
        ) from exc

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise OracleSystemAuthError(
            "Oracle Millennium System token response did not contain access_token.",
            http_status=response.status_code,
        )

    token_type = str(payload.get("token_type") or "Bearer").strip() or "Bearer"
    try:
        expires_in = max(1, int(payload.get("expires_in") or 300))
    except (TypeError, ValueError):
        expires_in = 300
    scope = str(payload.get("scope") or " ".join(scopes)).strip()

    metadata = {
        "status": "ready",
        "httpStatus": response.status_code,
        "tokenType": token_type,
        "expiresIn": expires_in,
        "scope": scope,
        "tokenUrl": token_url,
        "cached": False,
    }
    return access_token, metadata


async def get_system_access_token(*, force_refresh: bool = False) -> tuple[str, dict[str, Any]]:
    """Acquire/cache an Oracle Millennium System access token.

    The token itself is returned only to backend callers. Metadata returned to
    diagnostics deliberately excludes the bearer value and all client secrets.
    """
    global _cache

    # Backward compatibility only. When the new System app is configured, its
    # client_credentials flow always takes precedence over this legacy value.
    readiness = system_auth_readiness()
    if not readiness["configured"]:
        legacy = _value("ORACLE_MILLENNIUM_BEARER_TOKEN")
        if legacy:
            return legacy, {
                "status": "ready",
                "source": "legacy_static_bearer",
                "cached": True,
                "warning": "Prefer ORACLE_MESSAGING_SYSTEM_* client_credentials configuration.",
            }

    skew = float(_value("ORACLE_MESSAGING_TOKEN_REFRESH_SKEW_SECONDS") or "60")
    now = time.time()
    if (
        not force_refresh
        and _cache is not None
        and _cache.expires_at_epoch - max(0.0, skew) > now
    ):
        return _cache.access_token, {
            "status": "ready",
            "source": "system_client_credentials",
            "tokenType": _cache.token_type,
            "expiresAtEpoch": _cache.expires_at_epoch,
            "scope": _cache.scope,
            "tokenUrl": system_token_url(),
            "cached": True,
        }

    async with _lock:
        now = time.time()
        if (
            not force_refresh
            and _cache is not None
            and _cache.expires_at_epoch - max(0.0, skew) > now
        ):
            return _cache.access_token, {
                "status": "ready",
                "source": "system_client_credentials",
                "tokenType": _cache.token_type,
                "expiresAtEpoch": _cache.expires_at_epoch,
                "scope": _cache.scope,
                "tokenUrl": system_token_url(),
                "cached": True,
            }

        access_token, metadata = await _request_system_token()
        expires_in = int(metadata.get("expiresIn") or 300)
        _cache = _CachedToken(
            access_token=access_token,
            token_type=str(metadata.get("tokenType") or "Bearer"),
            expires_at_epoch=time.time() + expires_in,
            scope=str(metadata.get("scope") or ""),
        )
        metadata.update(
            {
                "source": "system_client_credentials",
                "expiresAtEpoch": _cache.expires_at_epoch,
            }
        )
        return access_token, metadata


async def test_system_token() -> dict[str, Any]:
    """Safe public diagnostic. Never includes the access token or secret."""
    try:
        _, metadata = await get_system_access_token(force_refresh=True)
        return metadata
    except OracleSystemAuthError as exc:
        return {
            "status": "failed",
            "httpStatus": exc.http_status,
            "error": str(exc),
            "oracleResponse": exc.response_excerpt,
            "tokenUrl": system_token_url() or None,
        }
