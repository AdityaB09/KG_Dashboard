import asyncio
import json
from typing import Any
import hashlib
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from app.physionet_waveforms import build_physionet_frame
from app.csv_waveforms import build_csv_waveform_frame
from app.api_range_waveforms import build_api_range_frame
from app.config import settings
from app.normalizer import FIELD_LABELS, now_iso, to_dashboard_frame
from app.oracle_smart import get_token_for_request, router as oracle_smart_router
from app.providers import (
    fetch_provider_medications,
    fetch_provider_observations,
    test_provider_status,
)

import math
import time
from app.incart_waveforms import build_incart_frame

from app.episodes import episode_coordinator
from app.episode_routes import router as episode_router

app = FastAPI(title="KardioGenics FHIR Streaming Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(oracle_smart_router)

app.include_router(episode_router)

@app.get("/health")
async def health():
    return {
        "ok": True,
        "provider": settings.FHIR_PROVIDER,
        "pollSeconds": settings.POLL_SECONDS,
        "fallbackDemoData": settings.USE_FALLBACK_DEMO_DATA,
        "firelyBaseUrl": settings.FIRELY_BASE_URL,
        "oracleMode": settings.ORACLE_MODE,
        "oracleBaseUrlConfigured": bool(settings.ORACLE_FHIR_BASE_URL),
        "oracleClientIdConfigured": bool(settings.ORACLE_CLIENT_ID),
    }


@app.get("/api/fhir/status")
async def fhir_status(
    provider: str = Query(default=settings.FHIR_PROVIDER),
):
    return await test_provider_status(provider)


@app.get("/api/fhir/oracle/status")
async def oracle_status(request: Request):
    token_state = get_token_for_request(request)

    return await test_provider_status(
        "oracle",
        access_token=token_state.get("access_token") if token_state else None,
        fhir_base_url=token_state.get("fhir_base_url") if token_state else None,
    )


@app.get("/api/fhir/oracle/session")
async def oracle_session_debug(request: Request):
    token_state = get_token_for_request(request)

    if not token_state:
        return {
            "hasOracleSession": False,
            "message": "No Oracle SMART session cookie found. Complete Oracle launch in this same browser."
        }

    return {
        "hasOracleSession": True,
        "provider": token_state.get("provider"),
        "fhirBaseUrl": token_state.get("fhir_base_url"),
        "hasAccessToken": bool(token_state.get("access_token")),
        "hasRefreshToken": bool(token_state.get("refresh_token")),
        "patientIdFromToken": token_state.get("patient_id"),
        "encounterIdFromToken": token_state.get("encounter_id"),
        "scope": token_state.get("scope"),
        "expiresAtEpoch": token_state.get("expires_at_epoch"),
    }



# @app.get("/api/firely/raw")
# async def raw_firely_observations(patient_id: str | None = Query(default=None)):
#     return await fetch_provider_observations("firely", patient_id)


# @app.get("/api/firely/latest")
# async def latest_firely_frame(
#     patient_id: str | None = Query(default=None),
#     debug: bool = Query(default=False),
# ):
#     bundle = await fetch_provider_observations("firely", patient_id)
#     return to_dashboard_frame(
#         bundle,
#         provider="firely-public-sandbox",
#         include_debug=debug,
#     )


# @app.get("/api/firely/debug/latest")
# async def latest_firely_debug_frame(patient_id: str | None = Query(default=None)):
#     bundle = await fetch_provider_observations("firely", patient_id)
#     return to_dashboard_frame(
#         bundle,
#         provider="firely-public-sandbox",
#         include_debug=True,
#     )


@app.get("/api/fhir/latest")
async def latest_fhir_frame(
    request: Request,
    provider: str = Query(default=settings.FHIR_PROVIDER),
    patient_id: str | None = Query(default=None),
    debug: bool = Query(default=False),
):
    token_state = get_token_for_request(request) if provider == "oracle" else None

    effective_patient_id = resolve_patient_id(
        provider=provider,
        requested_patient_id=patient_id,
        token_state=token_state,
    )

    access_token = token_state.get("access_token") if token_state else None
    fhir_base_url = token_state.get("fhir_base_url") if token_state else None

    observation_bundle = await fetch_provider_observations(
        provider,
        effective_patient_id,
        access_token=access_token,
        fhir_base_url=fhir_base_url,
    )

    medication_resources = await fetch_provider_medications(
        provider,
        effective_patient_id,
        access_token=access_token,
        fhir_base_url=fhir_base_url,
    )

    return to_dashboard_frame(
        observation_bundle,
        provider=provider_label(provider),
        include_debug=debug,
        medication_resources=medication_resources,
    )


# @app.get("/api/firely/stream")
# async def stream_firely_frame(
#     request: Request,
#     patient_id: str | None = Query(default=None),
#     debug: bool = Query(default=False),
# ):
#     # Old Firely route kept for compatibility.
#     return make_streaming_response(
#         request=request,
#         provider="firely",
#         patient_id=patient_id,
#         debug=debug,
#     )

@app.get("/api/firely/raw")
async def raw_firely_observations():
    return {
        "ok": False,
        "message": "Firely is disabled. Use Oracle via /api/stream or /api/fhir/latest?provider=oracle."
    }


@app.get("/api/firely/latest")
async def latest_firely_frame():
    return {
        "ok": False,
        "message": "Firely is disabled. Use Oracle via /api/fhir/latest?provider=oracle."
    }


@app.get("/api/firely/debug/latest")
async def latest_firely_debug_frame():
    return {
        "ok": False,
        "message": "Firely is disabled. Use Oracle via /api/fhir/latest?provider=oracle&debug=true."
    }


@app.get("/api/firely/stream")
async def stream_firely_frame():
    return {
        "ok": False,
        "message": "Firely is disabled. Use Oracle via /api/stream?debug=true."
    }
    
    
    
    
@app.get("/api/stream")
async def stream_fhir_frame(
    request: Request,
    debug: bool = Query(default=False),
):
    return make_streaming_response(
        request=request,
        provider="oracle",
        patient_id=None,
        debug=debug,
    )


def make_streaming_response(
    *,
    request: Request,
    provider: str,
    patient_id: str | None,
    debug: bool,
):
    async def event_generator():
        last_payload = None
        last_oracle_hash = None

        while True:
            try:
                token_state = get_token_for_request(request) if provider == "oracle" else None

                effective_patient_id = resolve_patient_id(
                    provider=provider,
                    requested_patient_id=patient_id,
                    token_state=token_state,
                )

                access_token = token_state.get("access_token") if token_state else None
                fhir_base_url = token_state.get("fhir_base_url") if token_state else None

                observation_bundle = await fetch_provider_observations(
                    provider,
                    effective_patient_id,
                    access_token=access_token,
                    fhir_base_url=fhir_base_url,
                )

                medication_resources = await fetch_provider_medications(
                    provider,
                    effective_patient_id,
                    access_token=access_token,
                    fhir_base_url=fhir_base_url,
                )

                frame = to_dashboard_frame(
                    observation_bundle,
                    provider=provider_label(provider),
                    include_debug=debug,
                    medication_resources=medication_resources,
                )

                oracle_values = frame.get("debug", {}).get("rawExtractedFhirValues") or {
                    "vitals": frame.get("vitals"),
                    "labs": frame.get("labs"),
                }

                oracle_hash = hashlib.sha256(
                    json.dumps(oracle_values, sort_keys=True).encode("utf-8")
                ).hexdigest()[:10]

                oracle_changed = oracle_hash != last_oracle_hash
                last_oracle_hash = oracle_hash

                quality = frame.get("dataQuality", {})

                print(
                    "[KGEN ORACLE SSE]",
                    f"provider={provider_label(provider)}",
                    f"patient={effective_patient_id}",
                    f"receivedAt={frame.get('receivedAt')}",
                    f"fhirFields={quality.get('fhirFields')}",
                    f"fallbackFields={quality.get('fallbackFields')}",
                    f"observationCount={quality.get('observationCount')}",
                    f"matchedObservationCount={quality.get('matchedObservationCount')}",
                    f"oracleHash={oracle_hash}",
                    f"oracleChanged={oracle_changed}",
                )

                payload = json.dumps(frame, separators=(",", ":"))

                if payload != last_payload:
                    last_payload = payload

                    yield "event: fhir-frame\n"
                    yield f"data: {payload}\n\n"

                    yield "event: firely-frame\n"
                    yield f"data: {payload}\n\n"
                else:
                    heartbeat = {
                        "status": "heartbeat",
                        "provider": provider,
                        "receivedAt": now_iso(),
                    }

                    yield "event: heartbeat\n"
                    yield f"data: {json.dumps(heartbeat)}\n\n"

            except Exception as error:
                error_frame = build_error_frame(provider, error)
                payload = json.dumps(error_frame, separators=(",", ":"))

                yield "event: fhir-frame\n"
                yield f"data: {payload}\n\n"

                yield "event: firely-frame\n"
                yield f"data: {payload}\n\n"

            await asyncio.sleep(settings.POLL_SECONDS)

    origin = request.headers.get("origin")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Vary": "Origin",
    }

    if origin in settings.FRONTEND_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )
def resolve_patient_id(
    *,
    provider: str,
    requested_patient_id: str | None,
    token_state: dict[str, Any] | None,
) -> str | None:
    if requested_patient_id:
        return requested_patient_id

    if provider == "oracle":
        if token_state and token_state.get("patient_id"):
            return token_state["patient_id"]

        # Allow a known sandbox patient id even in smart mode.
        if settings.ORACLE_TEST_PATIENT_ID:
            return settings.ORACLE_TEST_PATIENT_ID

    return None



def provider_label(provider: str) -> str:
    if provider == "firely":
        return "firely-public-sandbox"

    if provider == "oracle":
        return f"oracle-{settings.ORACLE_MODE}"

    return provider


def build_error_frame(provider: str, error: Exception) -> dict[str, Any]:
    return {
        "source": provider,
        "status": "error",
        "timestamp": now_iso(),
        "receivedAt": now_iso(),
        "overallColor": "yellow",
        "error": str(error),
        "vitals": {},
        "labs": {},
        "colors": {},
        "fallbackUsed": [],
        "dataQuality": {
            "fhirFieldCount": 0,
            "firelyFieldCount": 0,
            "fallbackFieldCount": 0,
            "fhirFields": [],
            "firelyFields": [],
            "fallbackFields": [],
            "missingRawFhirFields": list(FIELD_LABELS.keys()),
            "missingRawFirelyFields": list(FIELD_LABELS.keys()),
            "observationCount": 0,
            "matchedObservationCount": 0,
        },
        "interpretation": {
            "title": "FHIR stream warning",
            "rhythm": "The backend could not fetch the latest FHIR Observations.",
            "ppg": "The dashboard can continue showing local waveform simulation.",
            "likelyEtiology": "Check backend logs, provider configuration, SMART token state, network access, or patient_id filtering.",
        },
        "priorityTrends": [],
        "medicationRows": [],
        "contextAlerts": [],
    }
    
@app.get("/api/fhir/oracle/session")
async def oracle_session_debug(request: Request):
    token_state = get_token_for_request(request)

    if not token_state:
        return {
            "hasOracleSession": False,
            "message": "No Oracle SMART session cookie found. Complete /auth/oracle/launch in the same browser."
        }

    return {
        "hasOracleSession": True,
        "provider": token_state.get("provider"),
        "fhirBaseUrl": token_state.get("fhir_base_url"),
        "hasAccessToken": bool(token_state.get("access_token")),
        "hasRefreshToken": bool(token_state.get("refresh_token")),
        "patientIdFromToken": token_state.get("patient_id"),
        "encounterIdFromToken": token_state.get("encounter_id"),
        "scope": token_state.get("scope"),
        "expiresAtEpoch": token_state.get("expires_at_epoch"),
    }
    

@app.get("/api/fhir/oracle/raw/observations")
async def raw_oracle_observations(
    request: Request,
    patient_id: str | None = Query(default=None),
):
    token_state = get_token_for_request(request)

    effective_patient_id = resolve_patient_id(
        provider="oracle",
        requested_patient_id=patient_id,
        token_state=token_state,
    )

    bundle = await fetch_provider_observations(
        "oracle",
        effective_patient_id,
        access_token=token_state.get("access_token") if token_state else None,
        fhir_base_url=token_state.get("fhir_base_url") if token_state else None,
    )

    return {
        "provider": "oracle",
        "effectivePatientId": effective_patient_id,
        "bundleType": bundle.get("type"),
        "bundleTotal": bundle.get("total"),
        "entryCount": len(bundle.get("entry", []) or []),
        "rawBundle": bundle,
    }
    
    
    
@app.get("/api/waveforms/latest")
async def latest_waveform_frame(
    source: str = Query(default=settings.WAVEFORM_SOURCE),
):
    try:
        return await build_selected_waveform_frame(
            source=source,
            cursor=0,
            batch_size=max(
                1,
                int(settings.WAVEFORM_SAMPLE_RATE * 0.25),
            ),
        )
    except Exception as error:
        return {
            "source": source,
            "status": "error",
            "error": str(error),
        }


@app.get("/api/waveforms/stream")
async def stream_waveform_frame(
    request: Request,
    source: str = Query(
        default=settings.WAVEFORM_SOURCE
    ),
    session_id: str = Query(default="main"),
    batch_ms: int = Query(
        default=settings.WAVEFORM_BATCH_MS,
        ge=20,
        le=500,
    ),
):
    async def event_generator():
        batch_size = max(
            1,
            int(
                settings.WAVEFORM_SAMPLE_RATE
                * (batch_ms / 1000)
            ),
        )
        cursor = 0

        while True:
            if await request.is_disconnected():
                break

            try:
                frame = await build_selected_waveform_frame(
                    source=source,
                    cursor=cursor,
                    batch_size=batch_size,
                )

                cursor = int(
                    frame.get(
                        "nextCursor",
                        cursor + batch_size,
                    )
                )
                episode_coordinator.observe_frame(
    session_id=f"{session_id}:{source}",
    frame=frame,
)

                yield "event: waveform-frame\n"
                yield (
                    f"data: "
                    f"{json.dumps(frame, separators=(',', ':'))}"
                    f"\n\n"
                )

                await asyncio.sleep(batch_ms / 1000)

            except Exception as error:
                error_frame = {
                    "source": source,
                    "status": "error",
                    "error": str(error),
                    "sampleRate": settings.WAVEFORM_SAMPLE_RATE,
                    "batchSize": batch_size,
                    "leads": {},
                    "leadsMv": {},
                    "latestMv": {},
                    "vitals": {},
                }

                print(
                    "[KGEN WAVEFORM STREAM ERROR]",
                    str(error),
                )

                yield "event: waveform-frame\n"
                yield (
                    f"data: "
                    f"{json.dumps(error_frame, separators=(',', ':'))}"
                    f"\n\n"
                )

                await asyncio.sleep(1)

    origin = request.headers.get("origin")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Vary": "Origin",
    }

    if origin in settings.FRONTEND_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )

def build_waveform_frame(
    *,
    start_seconds: float,
    sample_rate: int,
    batch_size: int,
) -> dict[str, Any]:
    leads = {
        "lead1": [],
        "lead2": [],
        "lead3": [],
        "avr": [],
        "avl": [],
        "avf": [],
        "pleth": [],
    }

    for index in range(batch_size):
        t = start_seconds + index / sample_rate

        base = synthetic_ecg_value(t, bpm=72)
        leads["lead1"].append(base)
        leads["lead2"].append(synthetic_ecg_value(t + 0.018, bpm=72) * 0.92)
        leads["lead3"].append(synthetic_ecg_value(t + 0.032, bpm=72) * 0.78)
        leads["avr"].append(-synthetic_ecg_value(t + 0.014, bpm=72) * 0.72)
        leads["avl"].append(synthetic_ecg_value(t + 0.041, bpm=72) * 0.58)
        leads["avf"].append(synthetic_ecg_value(t + 0.027, bpm=72) * 0.84)
        leads["pleth"].append(synthetic_pleth_value(t, bpm=72))

    return {
        "source": "kardiogenics-waveform-demo",
        "status": "connected",
        "receivedAt": now_iso(),
        "sampleRate": sample_rate,
        "batchSize": batch_size,
        "leads": leads,
        "vitals": {
            "heartRate": round(72 + math.sin(start_seconds / 4) * 4),
            "spo2": round(97 + math.sin(start_seconds / 6)),
            "systolic": round(122 + math.sin(start_seconds / 5) * 5),
            "diastolic": round(78 + math.sin(start_seconds / 5) * 3),
            "respiratoryRate": round(16 + math.sin(start_seconds / 7) * 2),
            "temperature": round(37.0 + math.sin(start_seconds / 12) * 0.2, 1),
        },
    }


def synthetic_ecg_value(t: float, bpm: int = 72) -> float:
    beat = (t * bpm / 60.0) % 1.0

    value = 0.02 * math.sin(2 * math.pi * 7 * t)

    if 0.04 <= beat < 0.08:
        value += 0.12 * math.sin((beat - 0.04) / 0.04 * math.pi)

    if 0.10 <= beat < 0.13:
        value -= 0.18 * math.sin((beat - 0.10) / 0.03 * math.pi)

    if 0.13 <= beat < 0.17:
        value += 0.95 * math.sin((beat - 0.13) / 0.04 * math.pi)

    if 0.17 <= beat < 0.22:
        value -= 0.28 * math.sin((beat - 0.17) / 0.05 * math.pi)

    if 0.32 <= beat < 0.48:
        value += 0.20 * math.sin((beat - 0.32) / 0.16 * math.pi)

    return max(-1.0, min(1.0, value))


def synthetic_pleth_value(t: float, bpm: int = 72) -> float:
    beat = (t * bpm / 60.0) % 1.0

    if beat < 0.18:
        pulse = math.sin((beat / 0.18) * math.pi)
    else:
        pulse = math.exp(-(beat - 0.18) * 4.2)

    value = -0.45 + pulse * 0.85 + 0.03 * math.sin(2 * math.pi * 0.4 * t)
    return max(-1.0, min(1.0, value))

async def build_selected_waveform_frame(
    *,
    source: str,
    cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    selected_source = source.strip().lower()

    if selected_source == "csv":
        return build_csv_waveform_frame(
            cursor=cursor,
            batch_size=batch_size,
        )

    if selected_source in {"api_range", "api-range"}:
        return await build_api_range_frame(
            cursor=cursor,
            batch_size=batch_size,
        )

    if selected_source == "physionet":
        return build_physionet_frame(
            cursor=cursor,
            batch_size=batch_size,
        )
    if selected_source == "incart":
        return await build_incart_frame(
            cursor=cursor,
            batch_size=batch_size,
        )

    raise ValueError(
        "source must be physionet, csv, incart, or api_range"
    )