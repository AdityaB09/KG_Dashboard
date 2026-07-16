import asyncio
import hashlib
import json
import math
import time
import traceback
from typing import Any
import re
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api_range_waveforms import build_api_range_frame
from app.config import settings
from app.csv_waveforms import build_csv_waveform_frame
from app.episode_routes import incident_router, router as episode_router
from app.episodes import episode_coordinator
from app.fhir_http import bundle_resources, fhir_get
from app.incart_waveforms import build_incart_frame
from app.normalizer import (
    FIELD_LABELS,
    get_code_display,
    get_observation_timestamp,
    get_quantity_unit,
    get_quantity_value,
    now_iso,
    to_dashboard_frame,
    get_codes
)
from app.oracle_smart import (
    get_token_for_request,
    router as oracle_smart_router,
)
from app.physionet_waveforms import build_physionet_frame
from app.providers import (
    fetch_oracle_patient,
    fetch_oracle_patient_resources,
    fetch_provider_medications,
    fetch_provider_observations,
    test_provider_status,
    fetch_oracle_observations_by_codes,
)


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

app.include_router(incident_router)

def observe_episode_frame_safely(
    *,
    session_id: str,
    source: str,
    frame: dict[str, Any],
) -> None:
    try:
        episode_coordinator.observe_frame(
            session_id=f"{session_id}:{source}",
            frame=frame,
        )
    except Exception as error:
        print(
            "[KGEN EPISODE OBSERVER ERROR]",
            type(error).__name__,
            str(error),
        )
        traceback.print_exc()

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

@app.get(
    "/api/fhir/oracle/context-inventory"
)
async def oracle_context_inventory(
    request: Request,
):
    token_state = get_token_for_request(
        request
    )

    if not token_state:
        raise HTTPException(
            status_code=401,
            detail=(
                "Oracle SMART session "
                "is unavailable."
            ),
        )

    patient_id = token_state.get(
        "patient_id"
    )

    if not patient_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Oracle SMART session "
                "has no patient context."
            ),
        )

    access_token = token_state.get(
        "access_token"
    )

    base_url = token_state.get(
        "fhir_base_url"
    )

    (
        patient,
        observation_bundle,
        conditions,
        encounters,
        medication_requests,
        medication_administrations,
        medication_dispenses,
        diagnostic_reports,
        documents,
    ) = await asyncio.gather(
        fetch_oracle_patient(
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
        ),

        fetch_provider_observations(
            "oracle",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
        ),

        fetch_oracle_patient_resources(
            "Condition",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "Encounter",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "MedicationRequest",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "MedicationAdministration",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "MedicationDispense",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "DiagnosticReport",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),

        fetch_oracle_patient_resources(
            "DocumentReference",
            patient_id,
            access_token=access_token,
            fhir_base_url=base_url,
            count=100,
        ),
    )

    return {
        "patientId": patient_id,
        "availableDomains": {
            "patient": bool(patient),
            "observations": len(
                bundle_resources(
                    observation_bundle,
                    "Observation",
                )
            ),
            "conditions": len(conditions),
            "encounters": len(encounters),
            "medicationRequests": len(
                medication_requests
            ),
            "medicationAdministrations": len(
                medication_administrations
            ),
            "medicationDispenses": len(
                medication_dispenses
            ),
            "diagnosticReports": len(
                diagnostic_reports
            ),
            "documents": len(documents),
        },
        "observationCatalog": (
            build_observation_catalog(
                observation_bundle
            )
        ),
    }
    
    
@app.get(
    "/api/fhir/oracle/capabilities"
)
async def oracle_capabilities(
    request: Request,
):
    token_state = get_token_for_request(
        request
    )

    if not token_state:
        raise HTTPException(
            status_code=401,
            detail=(
                "Oracle SMART session "
                "is unavailable."
            ),
        )

    capability = await fhir_get(
        token_state["fhir_base_url"],
        "/metadata",
        access_token=token_state.get(
            "access_token"
        ),
    )

    resources = []

    for rest in (
        capability.get("rest", [])
        or []
    ):
        if rest.get("mode") != "server":
            continue

        for resource in (
            rest.get("resource", [])
            or []
        ):
            resources.append(
                {
                    "type": resource.get(
                        "type"
                    ),
                    "interactions": [
                        item.get("code")
                        for item in (
                            resource.get(
                                "interaction",
                                [],
                            )
                            or []
                        )
                    ],
                    "searchParameters": [
                        item.get("name")
                        for item in (
                            resource.get(
                                "searchParam",
                                [],
                            )
                            or []
                        )
                    ],
                }
            )

    return {
        "fhirVersion": capability.get(
            "fhirVersion"
        ),
        "software": capability.get(
            "software"
        ),
        "resources": sorted(
            resources,
            key=lambda item: (
                item.get("type") or ""
            ),
        ),
    }
    

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
    
    
def build_observation_catalog(
    bundle: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog: dict[
        str,
        dict[str, Any],
    ] = {}

    observations = bundle_resources(
        bundle,
        "Observation",
    )

    for observation in observations:
        candidates = [
            observation,
            *(
                observation.get(
                    "component",
                    [],
                )
                or []
            ),
        ]

        for candidate in candidates:
            codings = (
                candidate.get(
                    "code",
                    {},
                ).get(
                    "coding",
                    [],
                )
                or []
            )

            display = (
                candidate.get(
                    "code",
                    {},
                ).get("text")
                or get_code_display(
                    candidate
                )
            )

            if codings:
                first = codings[0]
                system = first.get("system")
                code = first.get("code")
                coding_display = first.get(
                    "display"
                )

                display = (
                    display
                    or coding_display
                    or code
                )
            else:
                system = None
                code = None

            key = (
                f"{system}|{code}"
                if code
                else display
            )

            if not key:
                continue

            row = catalog.setdefault(
                key,
                {
                    "system": system,
                    "code": code,
                    "display": display,
                    "count": 0,
                    "sampleValues": [],
                    "units": set(),
                    "latestAt": None,
                },
            )

            row["count"] += 1

            value = get_quantity_value(
                candidate
            )

            unit = get_quantity_unit(
                candidate
            )

            if value is not None:
                if (
                    len(
                        row["sampleValues"]
                    )
                    < 5
                ):
                    row[
                        "sampleValues"
                    ].append(value)

            if unit:
                row["units"].add(unit)

            timestamp = (
                get_observation_timestamp(
                    observation
                )
            )

            if (
                timestamp
                and (
                    row["latestAt"] is None
                    or timestamp
                    > row["latestAt"]
                )
            ):
                row["latestAt"] = timestamp

    output = []

    for row in catalog.values():
        output.append(
            {
                **row,
                "units": sorted(
                    row["units"]
                ),
            }
        )

    return sorted(
        output,
        key=lambda item: (
            -item["count"],
            str(item.get("display")),
        ),
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
                observe_episode_frame_safely(
    session_id=session_id,
    source=source,
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
    
    
LAB_DISCOVERY_PATTERNS = {
    "glucose": [
        r"\bglucose\b",
        r"\bblood sugar\b",
    ],
    "creatinine": [
        r"\bcreatinine\b",
    ],
    "wbc": [
        r"\bwbc\b",
        r"\bwhite blood cell\b",
        r"\bwhite blood count\b",
        r"\bleukocyte count\b",
        r"\bleukocytes\b",
    ],
}


KNOWN_MISSING_LAB_CODES = {
    "glucose": {
        "2339-0",
        "15074-8",
        "2345-7",
    },
    "creatinine": {
        "2160-0",
        "38483-4",
    },
    "wbc": {
        "6690-2",
        "26464-8",
    },
}


def bundle_next_url(
    bundle: dict[str, Any],
) -> str | None:
    for link in (
        bundle.get("link", [])
        or []
    ):
        if link.get("relation") == "next":
            return link.get("url")

    return None


def codeable_details(
    codeable: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(codeable, dict):
        return {
            "text": None,
            "codings": [],
            "searchText": "",
            "bareCodes": [],
        }

    text_parts = []
    codings = []
    bare_codes = []

    if codeable.get("text"):
        text_parts.append(
            str(codeable["text"])
        )

    for coding in (
        codeable.get("coding", [])
        or []
    ):
        system = coding.get("system")
        code = coding.get("code")
        display = coding.get("display")

        if display:
            text_parts.append(str(display))

        if code:
            text_parts.append(str(code))
            bare_codes.append(str(code))

        codings.append(
            {
                "system": system,
                "code": code,
                "display": display,
                "mappingValue": (
                    f"{system}|{code}"
                    if system and code
                    else code
                ),
            }
        )

    return {
        "text": codeable.get("text"),
        "codings": codings,
        "searchText": " ".join(
            text_parts
        ).lower(),
        "bareCodes": bare_codes,
    }


def observation_nodes(
    observation: dict[str, Any],
):
    yield (
        "observation",
        observation,
    )

    for index, component in enumerate(
        observation.get(
            "component",
            [],
        )
        or []
    ):
        yield (
            f"component[{index}]",
            component,
        )


def matched_lab_fields(
    search_text: str,
    bare_codes: list[str],
) -> list[str]:
    matches = []

    bare_code_set = set(bare_codes)

    for field, patterns in (
        LAB_DISCOVERY_PATTERNS.items()
    ):
        code_match = bool(
            bare_code_set.intersection(
                KNOWN_MISSING_LAB_CODES[
                    field
                ]
            )
        )

        text_match = any(
            re.search(
                pattern,
                search_text,
                flags=re.IGNORECASE,
            )
            for pattern in patterns
        )

        if not code_match and not text_match:
            continue

        if (
            field == "creatinine"
            and "clearance" in search_text
        ):
            continue

        if (
            field == "wbc"
            and "leukocyte esterase"
            in search_text
        ):
            continue

        matches.append(field)

    return matches


def node_value(
    node: dict[str, Any],
) -> tuple[Any, str | None]:
    quantity_value = get_quantity_value(
        node
    )

    quantity_unit = get_quantity_unit(
        node
    )

    if quantity_value is not None:
        return (
            quantity_value,
            quantity_unit,
        )

    for field_name in (
        "valueInteger",
        "valueDecimal",
        "valueString",
    ):
        if field_name in node:
            return (
                node.get(field_name),
                None,
            )

    codeable = node.get(
        "valueCodeableConcept"
    )

    if isinstance(codeable, dict):
        details = codeable_details(
            codeable
        )

        return (
            details.get("text")
            or details.get("searchText")
            or None,
            None,
        )

    return None, None


async def collect_paginated_resources(
    client: httpx.AsyncClient,
    *,
    initial_url: str,
    headers: dict[str, str],
    params: dict[str, Any],
    resource_type: str,
    max_pages: int,
) -> tuple[
    list[dict[str, Any]],
    int,
]:
    resources = []
    current_url = initial_url
    current_params = params
    page_count = 0

    while (
        current_url
        and page_count < max_pages
    ):
        response = await client.get(
            current_url,
            params=current_params,
            headers=headers,
        )

        response.raise_for_status()

        bundle = response.json()

        resources.extend(
            bundle_resources(
                bundle,
                resource_type,
            )
        )

        page_count += 1

        next_url = bundle_next_url(
            bundle
        )

        if not next_url:
            break

        current_url = urljoin(
            current_url,
            next_url,
        )

        current_params = None

    return resources, page_count



@app.get(
    "/api/fhir/oracle/"
    "discover-missing-lab-codes"
)
async def discover_missing_lab_codes(
    request: Request,
    max_pages: int = Query(
        default=20,
        ge=1,
        le=50,
    ),
):
    token_state = get_token_for_request(
        request
    )

    if not token_state:
        raise HTTPException(
            status_code=401,
            detail=(
                "Oracle SMART session "
                "is unavailable."
            ),
        )

    patient_id = token_state.get(
        "patient_id"
    )

    access_token = token_state.get(
        "access_token"
    )

    base_url = (
        token_state.get(
            "fhir_base_url"
        )
        or ""
    ).rstrip("/")

    if not patient_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "The SMART launch did not "
                "provide patient context."
            ),
        )

    if not access_token or not base_url:
        raise HTTPException(
            status_code=401,
            detail=(
                "Oracle token or FHIR base "
                "URL is unavailable."
            ),
        )

    headers = {
        "Accept": (
            "application/fhir+json, "
            "application/json"
        ),
        "Authorization": (
            f"Bearer {access_token}"
        ),
    }

    observations_by_id = {}
    diagnostic_reports = []
    errors = []
    search_stats = []

    observation_searches = [
        (
            "laboratory-coded-category",
            {
                "patient": patient_id,
                "category": (
                    "http://terminology.hl7.org/"
                    "CodeSystem/"
                    "observation-category"
                    "|laboratory"
                ),
                "_count": "100",
                "_sort": "-date",
            },
        ),
        (
            "laboratory-category",
            {
                "patient": patient_id,
                "category": "laboratory",
                "_count": "100",
                "_sort": "-date",
            },
        ),
        (
            "all-observations",
            {
                "patient": patient_id,
                "_count": "100",
                "_sort": "-date",
            },
        ),
    ]

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
    ) as client:
        for search_name, params in (
            observation_searches
        ):
            try:
                resources, pages = (
                    await collect_paginated_resources(
                        client,
                        initial_url=(
                            f"{base_url}/Observation"
                        ),
                        headers=headers,
                        params=params,
                        resource_type=(
                            "Observation"
                        ),
                        max_pages=max_pages,
                    )
                )

                search_stats.append(
                    {
                        "search": search_name,
                        "pages": pages,
                        "resources": len(
                            resources
                        ),
                    }
                )

                for observation in resources:
                    observation_id = (
                        observation.get("id")
                    )

                    key = (
                        observation_id
                        or json.dumps(
                            observation,
                            sort_keys=True,
                            default=str,
                        )
                    )

                    observations_by_id[key] = (
                        observation
                    )

            except Exception as error:
                errors.append(
                    {
                        "search": search_name,
                        "errorType": (
                            type(error).__name__
                        ),
                        "message": str(error),
                    }
                )

        try:
            diagnostic_reports, report_pages = (
                await collect_paginated_resources(
                    client,
                    initial_url=(
                        f"{base_url}/"
                        "DiagnosticReport"
                    ),
                    headers=headers,
                    params={
                        "patient": patient_id,
                        "_count": "100",
                        "_sort": "-date",
                    },
                    resource_type=(
                        "DiagnosticReport"
                    ),
                    max_pages=max_pages,
                )
            )

            search_stats.append(
                {
                    "search": (
                        "diagnostic-reports"
                    ),
                    "pages": report_pages,
                    "resources": len(
                        diagnostic_reports
                    ),
                }
            )

        except Exception as error:
            diagnostic_reports = []

            errors.append(
                {
                    "search": (
                        "diagnostic-reports"
                    ),
                    "errorType": (
                        type(error).__name__
                    ),
                    "message": str(error),
                }
            )

        referenced_observation_count = 0

        for report in diagnostic_reports:
            for result in (
                report.get(
                    "result",
                    [],
                )
                or []
            ):
                reference = result.get(
                    "reference"
                )

                if not reference:
                    continue

                if reference.startswith("#"):
                    continue

                if (
                    "Observation/"
                    not in reference
                ):
                    continue

                observation_url = (
                    reference
                    if reference.startswith(
                        "http://"
                    )
                    or reference.startswith(
                        "https://"
                    )
                    else urljoin(
                        f"{base_url}/",
                        reference,
                    )
                )

                try:
                    response = await client.get(
                        observation_url,
                        headers=headers,
                    )

                    response.raise_for_status()

                    observation = response.json()

                    if (
                        observation.get(
                            "resourceType"
                        )
                        != "Observation"
                    ):
                        continue

                    observation_id = (
                        observation.get("id")
                    )

                    key = (
                        observation_id
                        or observation_url
                    )

                    if (
                        key not in
                        observations_by_id
                    ):
                        referenced_observation_count += 1

                    observations_by_id[key] = (
                        observation
                    )

                except Exception as error:
                    errors.append(
                        {
                            "search": (
                                "DiagnosticReport.result"
                            ),
                            "reference": reference,
                            "errorType": (
                                type(error).__name__
                            ),
                            "message": str(error),
                        }
                    )

    candidates_by_field = {
        "glucose": [],
        "creatinine": [],
        "wbc": [],
    }

    seen_candidates = set()

    for observation in (
        observations_by_id.values()
    ):
        timestamp = (
            get_observation_timestamp(
                observation
            )
        )

        for location, node in (
            observation_nodes(
                observation
            )
        ):
            details = codeable_details(
                node.get("code")
            )

            matches = matched_lab_fields(
                details["searchText"],
                details["bareCodes"],
            )

            if not matches:
                continue

            value, unit = node_value(
                node
            )

            mappings = [
                item.get("mappingValue")
                for item in details[
                    "codings"
                ]
                if item.get(
                    "mappingValue"
                )
            ]

            for matched_field in matches:
                candidate_key = (
                    matched_field,
                    observation.get("id"),
                    location,
                    tuple(mappings),
                    str(value),
                    unit,
                )

                if candidate_key in (
                    seen_candidates
                ):
                    continue

                seen_candidates.add(
                    candidate_key
                )

                candidates_by_field[
                    matched_field
                ].append(
                    {
                        "observationId": (
                            observation.get(
                                "id"
                            )
                        ),
                        "location": location,
                        "status": (
                            observation.get(
                                "status"
                            )
                        ),
                        "displayText": (
                            details.get("text")
                            or details.get(
                                "searchText"
                            )
                        ),
                        "codings": (
                            details["codings"]
                        ),
                        "recommendedMappings": (
                            mappings
                        ),
                        "value": value,
                        "unit": unit,
                        "effectiveAt": (
                            timestamp
                        ),
                    }
                )

    recommended_mappings = {}

    for field, candidates in (
        candidates_by_field.items()
    ):
        recommended_mappings[field] = (
            sorted(
                {
                    mapping
                    for candidate
                    in candidates
                    for mapping
                    in candidate.get(
                        "recommendedMappings",
                        [],
                    )
                    if mapping
                }
            )
        )

    return {
        "patientId": patient_id,
        "observationCountScanned": len(
            observations_by_id
        ),
        "diagnosticReportCount": len(
            diagnostic_reports
        ),
        "diagnosticReportReferencedObservationsAdded": (
            referenced_observation_count
        ),
        "candidateCounts": {
            field: len(candidates)
            for field, candidates
            in candidates_by_field.items()
        },
        "recommendedMappings": (
            recommended_mappings
        ),
        "candidates": (
            candidates_by_field
        ),
        "searchStats": search_stats,
        "errors": errors,
        "interpretation": {
            "zeroCandidates": (
                "No matching structured "
                "Observation was found for "
                "this launched sandbox patient."
            ),
            "creatinineWarning": (
                "Do not map estimated "
                "creatinine clearance as "
                "serum creatinine."
            ),
            "wbcWarning": (
                "Do not map leukocyte "
                "esterase as WBC count."
            ),
        },
    }


@app.get(
    "/api/fhir/oracle/targeted-labs"
)
async def oracle_targeted_labs(
    request: Request,
):
    token_state = get_token_for_request(
        request
    )

    if not token_state:
        raise HTTPException(
            status_code=401,
            detail="Oracle session unavailable.",
        )

    patient_id = token_state.get(
        "patient_id"
    )

    if not patient_id:
        raise HTTPException(
            status_code=400,
            detail="Patient context unavailable.",
        )

    code_groups = {
        "glucose": [
            "2339-0",
            "15074-8",
            "2345-7",
        ],
        "creatinine": [
            "2160-0",
            "38483-4",
        ],
        "wbc": [
            "6690-2",
            "26464-8",
        ],
    }

    bundle = (
        await fetch_oracle_observations_by_codes(
            patient_id,
            code_groups,
            access_token=token_state.get(
                "access_token"
            ),
            fhir_base_url=token_state.get(
                "fhir_base_url"
            ),
            count=100,
        )
    )

    resources = bundle_resources(
        bundle,
        "Observation",
    )

    results = []

    for observation in resources:
        results.append(
            {
                "id": observation.get("id"),
                "display": get_code_display(
                    observation
                ),
                "codes": sorted(
                    get_codes(
                        observation.get("code")
                    )
                ),
                "value": get_quantity_value(
                    observation
                ),
                "unit": get_quantity_unit(
                    observation
                ),
                "timestamp": (
                    get_observation_timestamp(
                        observation
                    )
                ),
            }
        )

    return {
        "patientId": patient_id,
        "resultCount": len(results),
        "results": results,
    }