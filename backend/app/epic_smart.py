from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.fhir_http import fhir_get, fhir_post_form

router = APIRouter(tags=["epic-smart"])

# Kept completely separate from Oracle so the two EHR integrations cannot
# overwrite each other's auth state or session cookies.
EPIC_SMART_AUTH_STATE: dict[str, dict[str, Any]] = {}
EPIC_SMART_TOKEN_STORE: dict[str, dict[str, Any]] = {}

serializer = URLSafeSerializer(settings.SESSION_SECRET_KEY, salt="kardiogenics-epic-smart")


def _normalize_issuer(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _validate_issuer(issuer: str) -> None:
    allowed = {_normalize_issuer(item).casefold() for item in settings.EPIC_ALLOWED_ISSUERS}
    if allowed and _normalize_issuer(issuer).casefold() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                "The Epic SMART issuer is not allowlisted. "
                "Add the exact Epic FHIR base URL to EPIC_ALLOWED_ISSUERS."
            ),
        )


def create_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def create_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def create_session_id() -> str:
    return secrets.token_urlsafe(32)


def sign_session_id(session_id: str) -> str:
    return serializer.dumps({"sid": session_id})


def unsign_session_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return serializer.loads(value).get("sid")
    except BadSignature:
        return None


def get_epic_token_for_request(request: Request) -> dict[str, Any] | None:
    session_id = unsign_session_id(request.cookies.get("kardiogenics_epic_session"))
    if not session_id:
        return None
    return EPIC_SMART_TOKEN_STORE.get(session_id)


def _frontend_redirect_url() -> str:
    base = os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip()
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["mode"] = "epic-evaluation-auto"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def discover_smart_configuration(fhir_base_url: str) -> dict[str, Any]:
    return await fhir_get(fhir_base_url.rstrip("/"), "/.well-known/smart-configuration")


@router.get("/auth/epic/launch")
async def epic_launch(
    iss: str | None = Query(default=None),
    launch: str | None = Query(default=None),
):
    fhir_base_url = _normalize_issuer(iss or settings.EPIC_FHIR_BASE_URL)
    if not fhir_base_url:
        raise HTTPException(status_code=400, detail="Missing Epic SMART iss/FHIR base URL.")
    _validate_issuer(fhir_base_url)

    client_id = str(settings.EPIC_CLIENT_ID or "").strip()
    if not client_id:
        raise HTTPException(status_code=500, detail="EPIC_CLIENT_ID is not configured.")

    smart_config = await discover_smart_configuration(fhir_base_url)
    authorization_endpoint = smart_config.get("authorization_endpoint")
    token_endpoint = smart_config.get("token_endpoint")
    if not authorization_endpoint or not token_endpoint:
        raise HTTPException(status_code=500, detail="Epic SMART discovery did not return authorize/token endpoints.")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = create_code_verifier()
    code_challenge = create_code_challenge(code_verifier)

    EPIC_SMART_AUTH_STATE[state] = {
        "issuer": fhir_base_url,
        "fhir_base_url": fhir_base_url,
        "launch": launch,
        "token_endpoint": token_endpoint,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "created_at_epoch": time.time(),
        "expires_at_epoch": time.time() + 600,
    }

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.EPIC_REDIRECT_URI,
        "scope": settings.EPIC_SCOPES,
        "state": state,
        "aud": fhir_base_url,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if launch:
        params["launch"] = launch

    print("[EPIC SMART LAUNCH]", {
        "iss": fhir_base_url,
        "hasLaunch": bool(launch),
        "redirectUri": settings.EPIC_REDIRECT_URI,
        "clientId": client_id,
    })
    return RedirectResponse(f"{authorization_endpoint}?{urlencode(params)}")


@router.get("/auth/epic/callback")
async def epic_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    if error:
        return HTMLResponse(
            f"<h2>Epic SMART authorization failed</h2><p><b>Error:</b> {error}</p><p>{error_description or ''}</p>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")

    auth_state = EPIC_SMART_AUTH_STATE.pop(state, None)
    if not auth_state:
        raise HTTPException(status_code=400, detail="Invalid or expired Epic SMART state.")
    if float(auth_state.get("expires_at_epoch") or 0) < time.time():
        raise HTTPException(status_code=400, detail="Expired Epic SMART state.")

    token_response = await fhir_post_form(
        auth_state["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.EPIC_REDIRECT_URI,
            "client_id": settings.EPIC_CLIENT_ID,
            "code_verifier": auth_state["code_verifier"],
        },
    )

    session_id = create_session_id()
    EPIC_SMART_TOKEN_STORE[session_id] = {
        "provider": "epic",
        "fhir_base_url": auth_state["fhir_base_url"],
        "issuer": auth_state["issuer"],
        "token_endpoint": auth_state["token_endpoint"],
        "smart_session_id": session_id,
        "access_token": token_response.get("access_token"),
        "refresh_token": token_response.get("refresh_token"),
        "expires_at_epoch": time.time() + int(token_response.get("expires_in", 570)),
        "scope": token_response.get("scope"),
        "patient_id": token_response.get("patient"),
        "encounter_id": token_response.get("encounter"),
        "fhir_user": token_response.get("fhirUser") or token_response.get("fhir_user"),
        "id_token": token_response.get("id_token"),
        "created_at_epoch": time.time(),
    }

    frontend = _frontend_redirect_url()
    response = HTMLResponse(
        "<h2>Epic SMART connected</h2>"
        "<p>The selected Epic patient context is authenticated.</p>"
        "<p>CARDINAL is preparing the automatic evaluation demonstration.</p>"
        f"<script>setTimeout(() => window.location.href = {json.dumps(frontend)}, 700);</script>"
    )
    is_production = os.getenv("ENVIRONMENT", "development").lower() == "production"
    response.set_cookie(
        key="kardiogenics_epic_session",
        value=sign_session_id(session_id),
        httponly=True,
        secure=is_production,
        samesite="none" if is_production else "lax",
        max_age=60 * 60,
    )
    return response


@router.get("/auth/epic/logout")
async def epic_logout(request: Request):
    session_id = unsign_session_id(request.cookies.get("kardiogenics_epic_session"))
    if session_id:
        EPIC_SMART_TOKEN_STORE.pop(session_id, None)
    response = HTMLResponse("<h2>Epic SMART session cleared.</h2>")
    response.delete_cookie("kardiogenics_epic_session")
    return response
