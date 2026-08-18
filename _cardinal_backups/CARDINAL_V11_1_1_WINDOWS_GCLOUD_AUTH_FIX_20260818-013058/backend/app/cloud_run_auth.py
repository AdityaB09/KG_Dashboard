from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import subprocess
from typing import MutableMapping
from urllib.parse import quote

import httpx

_METADATA_IDENTITY = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)
_CACHE: dict[str, tuple[str, float]] = {}
_LOCK = asyncio.Lock()


def _auth_mode() -> str:
    return os.getenv("SLM_AUTH_MODE", "none").strip().lower() or "none"


def _audience(base_url: str) -> str:
    configured = os.getenv("SLM_AUTH_AUDIENCE", "").strip().rstrip("/")
    if configured:
        return configured
    return base_url.strip().rstrip("/")


def _jwt_exp(token: str) -> float:
    try:
        body = token.split(".", 2)[1]
        body += "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        return float(payload.get("exp") or 0)
    except Exception:
        return 0.0


async def _metadata_identity_token(audience: str) -> str:
    now = time.time()
    cached = _CACHE.get(audience)
    if cached and cached[1] - 300 > now:
        return cached[0]

    async with _LOCK:
        cached = _CACHE.get(audience)
        if cached and cached[1] - 300 > time.time():
            return cached[0]

        url = f"{_METADATA_IDENTITY}?audience={quote(audience, safe='')}&format=full"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers={"Metadata-Flavor": "Google"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "SLM_AUTH_MODE=gcp_identity is enabled, but Cloud Run identity "
                f"token acquisition failed for audience={audience!r}: {exc!r}"
            ) from exc

        token = response.text.strip()
        if not token:
            raise RuntimeError("Cloud Run metadata server returned an empty identity token.")
        exp = _jwt_exp(token) or (time.time() + 3000)
        _CACHE[audience] = (token, exp)
        return token


async def _gcloud_cli_identity_token(audience: str) -> str:
    """Acquire an identity token from the local gcloud CLI for Windows/local dev."""
    cache_key = f"gcloud:{audience}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and cached[1] - 120 > now:
        return cached[0]

    def _run() -> str:
        command = ["gcloud", "auth", "print-identity-token"]
        # --audiences is required for some service-account flows but rejected for
        # normal user credentials, so the standard user command matches the user's
        # proven Cloud Run curl workflow. SLM_AUTH_AUDIENCE remains used by metadata mode.
        proc = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "gcloud identity-token command failed").strip())
        return proc.stdout.strip()

    token = await asyncio.to_thread(_run)
    if not token:
        raise RuntimeError("gcloud returned an empty identity token.")
    exp = _jwt_exp(token) or (time.time() + 2700)
    _CACHE[cache_key] = (token, exp)
    return token


async def apply_slm_auth(
    headers: MutableMapping[str, str],
    *,
    base_url: str,
) -> MutableMapping[str, str]:
    """Apply deployment-only SLM authentication without changing local behavior.

    none/api_key/bearer: preserve the caller's existing Authorization behavior.
    gcp_identity: use the Cloud Run metadata identity token when CARDINAL is deployed.
    gcloud_cli: use `gcloud auth print-identity-token` for local Windows development.
    """
    mode = _auth_mode()
    if mode in {"", "none", "api_key", "bearer"}:
        return headers

    audience = _audience(base_url)
    if mode == "gcp_identity":
        token = await _metadata_identity_token(audience)
    elif mode == "gcloud_cli":
        token = await _gcloud_cli_identity_token(audience)
    else:
        raise RuntimeError(f"Unsupported SLM_AUTH_MODE={mode!r}.")
    headers["Authorization"] = f"Bearer {token}"
    return headers
