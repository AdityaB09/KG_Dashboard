from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request


_SEEN_JTI: dict[str, float] = {}


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


@dataclass(frozen=True)
class CdsSecurityContext:
    enabled: bool
    correlation_id: str
    issuer: str | None = None
    subject: str | None = None
    jti: str | None = None


def security_readiness() -> dict[str, Any]:
    enabled = _truthy("EPIC_CDS_AUTH_ENABLED")
    if not enabled:
        return {
            "state": "OPTIONAL",
            "enabled": False,
            "detail": "JWT validation is disabled for local/manual CDS testing.",
        }
    issuers = _csv("EPIC_CDS_ALLOWED_ISSUERS")
    jwks = _csv("EPIC_CDS_JWKS_URLS")
    audience = os.getenv("EPIC_CDS_AUDIENCE", "").strip()
    subjects = _csv("EPIC_CDS_ALLOWED_SUBJECTS")
    missing = []
    if not issuers:
        missing.append("EPIC_CDS_ALLOWED_ISSUERS")
    if not jwks:
        missing.append("EPIC_CDS_JWKS_URLS")
    if not audience:
        missing.append("EPIC_CDS_AUDIENCE")
    if not subjects:
        missing.append("EPIC_CDS_ALLOWED_SUBJECTS")
    return {
        "state": "READY" if not missing else "MISCONFIGURED",
        "enabled": True,
        "missing": missing,
        "issuers": issuers,
        "jwksUrls": jwks,
        "audienceConfigured": bool(audience),
        "subjectsConfigured": bool(subjects),
    }


def _purge_seen(now: float) -> None:
    stale = [key for key, expires_at in _SEEN_JTI.items() if expires_at <= now]
    for key in stale:
        _SEEN_JTI.pop(key, None)


def validate_epic_cds_request(request: Request) -> CdsSecurityContext:
    correlation_id = (
        request.headers.get("x-correlation-id")
        or request.headers.get("x-request-id")
        or f"cds-{uuid4().hex}"
    )
    if not _truthy("EPIC_CDS_AUTH_ENABLED"):
        return CdsSecurityContext(enabled=False, correlation_id=correlation_id)

    readiness = security_readiness()
    if readiness.get("state") != "READY":
        raise HTTPException(status_code=503, detail={"error": "EPIC_CDS_SECURITY_MISCONFIGURED", **readiness})

    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing CDS Authorization Bearer JWT.")
    token = authorization.split(" ", 1)[1].strip()

    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - only when dependency is missing
        raise HTTPException(status_code=503, detail="PyJWT[crypto] is required when EPIC_CDS_AUTH_ENABLED=true.") from exc

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid CDS JWT header: {type(exc).__name__}") from exc

    allowed_jwks = _csv("EPIC_CDS_JWKS_URLS")
    token_jku = str(header.get("jku") or "").strip()
    if token_jku:
        if token_jku not in allowed_jwks:
            raise HTTPException(status_code=401, detail="CDS JWT jku is not in the configured allowlist.")
        jwks_url = token_jku
    elif len(allowed_jwks) == 1:
        jwks_url = allowed_jwks[0]
    else:
        raise HTTPException(status_code=401, detail="CDS JWT does not identify an allowed JWK Set URL.")

    algorithm = str(header.get("alg") or "").upper()
    allowed_algorithms = ["RS256", "RS384", "RS512", "ES256", "ES384"]
    if algorithm not in allowed_algorithms:
        raise HTTPException(status_code=401, detail="CDS JWT signing algorithm is not allowed.")

    issuers = _csv("EPIC_CDS_ALLOWED_ISSUERS")
    subjects = _csv("EPIC_CDS_ALLOWED_SUBJECTS")
    audience = os.getenv("EPIC_CDS_AUDIENCE", "").strip()

    try:
        signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=allowed_algorithms,
            audience=audience,
            issuer=issuers[0] if len(issuers) == 1 else None,
            options={
                "require": ["exp", "nbf", "iat", "iss", "sub", "aud", "jti"],
                "verify_iss": len(issuers) == 1,
            },
            leeway=float(os.getenv("EPIC_CDS_JWT_LEEWAY_SECONDS", "60")),
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"CDS JWT validation failed: {type(exc).__name__}") from exc

    issuer = str(claims.get("iss") or "")
    subject = str(claims.get("sub") or "")
    jti = str(claims.get("jti") or "")
    if issuer not in issuers:
        raise HTTPException(status_code=401, detail="CDS JWT issuer is not allowed.")
    if subject not in subjects:
        raise HTTPException(status_code=401, detail="CDS JWT subject/client is not allowed.")

    now = time.time()
    _purge_seen(now)
    if jti in _SEEN_JTI:
        raise HTTPException(status_code=401, detail="CDS JWT jti has already been used.")
    _SEEN_JTI[jti] = float(claims.get("exp") or now + 300)

    return CdsSecurityContext(
        enabled=True,
        correlation_id=correlation_id,
        issuer=issuer,
        subject=subject,
        jti=jti,
    )
