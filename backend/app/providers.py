import json
from typing import Any

import httpx
from fastapi import HTTPException
from urllib.parse import urljoin
from app.config import settings
from app.fhir_http import fhir_get, bundle_resources

async def fetch_oracle_paginated_observations(
    patient_id: str,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
    category: str | None = None,
    count: int = 100,
    max_pages: int = 10,
) -> dict[str, Any]:
    base_url = (
        fhir_base_url
        or settings.ORACLE_FHIR_BASE_URL
    ).rstrip("/")

    if (
        not base_url
        or not patient_id
        or not access_token
    ):
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 0,
            "entry": [],
        }

    headers = {
        "Accept": (
            "application/fhir+json, "
            "application/json"
        ),
        "Authorization": (
            f"Bearer {access_token}"
        ),
    }

    params = {
        "patient": patient_id,
        "_count": str(count),
        "_sort": "-date",
    }

    if category:
        params["category"] = category

    current_url = (
        f"{base_url}/Observation"
    )

    current_params: (
        dict[str, Any] | None
    ) = params

    page_count = 0
    result_bundles = []

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
    ) as client:
        while (
            current_url
            and page_count < max_pages
        ):
            try:
                response = await client.get(
                    current_url,
                    params=current_params,
                    headers=headers,
                )

                response.raise_for_status()

                bundle = response.json()

                result_bundles.append(bundle)
                page_count += 1

                next_url = None

                for link in (
                    bundle.get("link", [])
                    or []
                ):
                    if (
                        link.get("relation")
                        == "next"
                    ):
                        next_url = link.get(
                            "url"
                        )
                        break

                if not next_url:
                    break

                current_url = urljoin(
                    current_url,
                    next_url,
                )

                current_params = None

            except httpx.TimeoutException as error:
                print(
                    "[KGEN PAGINATED LAB TIMEOUT]",
                    {
                        "page": page_count + 1,
                        "message": str(error),
                    },
                )
                break

            except httpx.HTTPStatusError as error:
                print(
                    "[KGEN PAGINATED LAB HTTP ERROR]",
                    {
                        "page": page_count + 1,
                        "status": (
                            error.response
                            .status_code
                        ),
                    },
                )
                break

            except Exception as error:
                print(
                    "[KGEN PAGINATED LAB ERROR]",
                    {
                        "page": page_count + 1,
                        "errorType": (
                            type(error).__name__
                        ),
                        "message": str(error),
                    },
                )
                break

    merged = merge_fhir_bundles(
        *result_bundles
    )

    merged["pagesFetched"] = page_count
    merged["categoryRequested"] = category

    return merged


async def fetch_firely_observations(patient_id: str | None = None) -> dict[str, Any]:
    params = {
        "_sort": "-_lastUpdated",
        "_count": "200",
    }

    if patient_id:
        params["subject"] = f"Patient/{patient_id}"

    if settings.DEBUG_FHIR_LOGS:
        print("\n[FHIR REQUEST] provider=firely resource=Observation")
        print("BASE:", settings.FIRELY_BASE_URL)
        print("PARAMS:", params)
        
        

    bundle = await fhir_get(
        settings.FIRELY_BASE_URL,
        "/Observation",
        params=params,
    )

    if settings.DEBUG_FHIR_LOGS:
        print("[FHIR RESPONSE] provider=firely Observation")
        print("BUNDLE TOTAL:", bundle.get("total"))
        print("ENTRY COUNT:", len(bundle.get("entry", []) or []))

    return bundle


async def fetch_oracle_observations(
    patient_id: str | None = None,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
) -> dict[str, Any]:
    base_url = (fhir_base_url or settings.ORACLE_FHIR_BASE_URL).rstrip("/")

    if not base_url:
        raise HTTPException(
            status_code=500,
            detail="ORACLE_FHIR_BASE_URL is not configured.",
        )

    # IMPORTANT:
    # In standalone SMART login with user/... scopes, Oracle may not give patient context.
    # Do not call /Observation?_count=200 without patient context because Oracle rejects it.
    if not patient_id:
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 0,
            "entry": [],
            "issue": [
                {
                    "severity": "information",
                    "code": "informational",
                    "diagnostics": (
                        "No Oracle patient_id is available. "
                        "The backend skipped Oracle Observation search to avoid a 400 response. "
                        "Use EHR launch patient context or provide a known Oracle sandbox patient id."
                    ),
                }
            ],
        }

    search_attempts = [
        {
            "_count": "200",
            "_sort": "-date",
            "patient": patient_id,
        },
        {
            "_count": "200",
            "patient": patient_id,
        },
        {
            "_count": "200",
            "subject": f"Patient/{patient_id}",
        },
    ]

    last_error: Exception | None = None

    for params in search_attempts:
        if settings.DEBUG_FHIR_LOGS:
            print("\n[FHIR REQUEST] provider=oracle resource=Observation")
            print("BASE:", base_url)
            print("PARAMS:", params)
            print("TOKEN:", "present" if access_token else "missing")

        try:
             return await fhir_get(
        base_url,
        "/Observation",
        params=params,
        access_token=access_token,
    )

        except httpx.HTTPStatusError as error:
            last_error = error

            status_code = (
        error.response.status_code
    )

            print(
        "[KGEN ORACLE OBSERVATION HTTP ERROR]",
        {
            "status": status_code,
            "params": params,
        },
    )

            if status_code in {
        400,
        404,
    }:
                continue

            raise

        except httpx.TimeoutException as error:
            last_error = error

            print(
        "[KGEN ORACLE OBSERVATION TIMEOUT]",
        {
            "params": params,
            "message": str(error),
        },
    )

            continue

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "entry": [],
        "issue": [
            {
                "severity": "warning",
                "code": "processing",
                "diagnostics": (
                    "Oracle Observation search failed for all patient search attempts. "
                    f"Last error: {str(last_error)}"
                ),
            }
        ],
    }


def merge_fhir_bundles(
    *bundles: dict[str, Any],
) -> dict[str, Any]:
    entries = []
    seen = set()

    for bundle in bundles:
        for entry in (
            bundle.get("entry", [])
            or []
        ):
            resource = entry.get("resource")

            if not isinstance(
                resource,
                dict,
            ):
                continue

            resource_type = resource.get(
                "resourceType"
            )

            resource_id = resource.get("id")

            if resource_type and resource_id:
                key = (
                    resource_type,
                    resource_id,
                )
            else:
                key = json.dumps(
                    resource,
                    sort_keys=True,
                    default=str,
                )

            if key in seen:
                continue

            seen.add(key)
            entries.append(entry)

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }


async def fetch_oracle_patient(
    patient_id: str,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
) -> dict[str, Any]:
    base_url = (
        fhir_base_url
        or settings.ORACLE_FHIR_BASE_URL
    ).rstrip("/")

    if not base_url or not patient_id:
        return {}

    try:
        return await fhir_get(
            base_url,
            f"/Patient/{patient_id}",
            access_token=access_token,
        )
    except Exception:
        return {}


async def fetch_oracle_observations_by_codes(
    patient_id: str,
    code_groups: dict[
        str,
        list[str],
    ],
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    base_url = (
        fhir_base_url
        or settings.ORACLE_FHIR_BASE_URL
    ).rstrip("/")

    if not base_url or not patient_id:
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 0,
            "entry": [],
        }

    result_bundles = []

    for field, codes in code_groups.items():
        for code in codes:
            tokens = [
                f"http://loinc.org|{code}",
                code,
            ]

            found_for_code = False

            for token in tokens:
                attempts = [
                    {
                        "patient": patient_id,
                        "code": token,
                        "_count": str(count),
                        "_sort": "-date",
                    },
                    {
                        "patient": patient_id,
                        "code": token,
                        "_count": str(count),
                    },
                    {
                        "subject": (
                            f"Patient/{patient_id}"
                        ),
                        "code": token,
                        "_count": str(count),
                    },
                ]

                for params in attempts:
                    try:
                        bundle = await fhir_get(
                            base_url,
                            "/Observation",
                            params=params,
                            access_token=(
                                access_token
                            ),
                        )

                        resources = bundle_resources(
                            bundle,
                            "Observation",
                        )

                        if resources:
                            result_bundles.append(
                                bundle
                            )

                            found_for_code = True
                            break

                    except httpx.HTTPStatusError as error:
                        print(
                            "[KGEN ORACLE CODE SEARCH SKIPPED]",
                            {
                                "field": field,
                                "code": code,
                                "status": (
                                    error.response.status_code
                                ),
                            },
                        )

                        continue

                    except httpx.TimeoutException as error:
                        print(
                            "[KGEN ORACLE CODE SEARCH TIMEOUT]",
                            {
                                "field": field,
                                "code": code,
                                "message": str(error),
                            },
                        )

                        continue

                    except Exception as error:
                        print(
                            "[KGEN ORACLE CODE SEARCH ERROR]",
                            {
                                "field": field,
                                "code": code,
                                "errorType": (
                                    type(error).__name__
                                ),
                                "message": str(error),
                            },
                        )

                        continue
                if found_for_code:
                    break

    return merge_fhir_bundles(
        *result_bundles
    )


async def fetch_oracle_patient_resources(
    resource_type: str,
    patient_id: str,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
    count: int = 50,
) -> list[dict[str, Any]]:
    base_url = (fhir_base_url or settings.ORACLE_FHIR_BASE_URL).rstrip("/")

    if not base_url:
        return []

    if not patient_id:
        return []

    params = {
        "patient": patient_id,
        "_count": str(count),
    }

    try:
        bundle = await fhir_get(
            base_url,
            f"/{resource_type}",
            params=params,
            access_token=access_token,
        )
        return bundle_resources(bundle, resource_type)

    except httpx.HTTPStatusError as error:
        # Retry with subject reference for resources that prefer subject.
        if error.response.status_code in {400, 404}:
            try:
                bundle = await fhir_get(
                    base_url,
                    f"/{resource_type}",
                    params={
                        "subject": f"Patient/{patient_id}",
                        "_count": str(count),
                    },
                    access_token=access_token,
                )
                return bundle_resources(bundle, resource_type)
            except Exception:
                return []

        return []

    except Exception:
        return []


async def fetch_provider_observations(
    provider: str,
    patient_id: str | None,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
) -> dict[str, Any]:
    return await fetch_oracle_observations(
        patient_id,
        access_token=access_token,
        fhir_base_url=fhir_base_url,
    )




async def fetch_provider_medications(
    provider: str,
    patient_id: str | None,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
) -> list[dict[str, Any]]:
    if not patient_id:
        return []

    resources: list[dict[str, Any]] = []

    for resource_type in [
        "MedicationRequest",
        "MedicationAdministration",
        "MedicationDispense",
    ]:
        resources.extend(
            await fetch_oracle_patient_resources(
                resource_type,
                patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
                count=25,
            )
        )

    return resources

async def test_provider_status(
    provider: str,
    *,
    access_token: str | None = None,
    fhir_base_url: str | None = None,
) -> dict[str, Any]:
    provider = provider.lower()

    if provider == "firely":
        metadata = await fhir_get(settings.FIRELY_BASE_URL, "/metadata")
        return {
            "provider": "firely",
            "ok": True,
            "baseUrl": settings.FIRELY_BASE_URL,
            "software": metadata.get("software", {}).get("name"),
            "fhirVersion": metadata.get("fhirVersion"),
        }

    if provider == "oracle":
        base_url = (fhir_base_url or settings.ORACLE_FHIR_BASE_URL).rstrip("/")
        if not base_url:
            return {
                "provider": "oracle",
                "ok": False,
                "error": "ORACLE_FHIR_BASE_URL is missing.",
            }

        result = {
            "provider": "oracle",
            "ok": True,
            "mode": settings.ORACLE_MODE,
            "baseUrl": base_url,
            "clientIdConfigured": bool(settings.ORACLE_CLIENT_ID),
        }

        try:
            smart_config = await fhir_get(
                base_url,
                "/.well-known/smart-configuration",
                access_token=access_token,
            )
            result["smartConfigurationAvailable"] = True
            result["authorizationEndpoint"] = smart_config.get("authorization_endpoint")
            result["tokenEndpoint"] = smart_config.get("token_endpoint")
            result["scopesSupported"] = smart_config.get("scopes_supported", [])
            result["codeChallengeMethodsSupported"] = smart_config.get("code_challenge_methods_supported", [])
        except Exception as error:
            result["smartConfigurationAvailable"] = False
            result["smartConfigurationError"] = str(error)

        try:
            metadata = await fhir_get(
                base_url,
                "/metadata",
                access_token=access_token,
            )
            result["metadataAvailable"] = True
            result["fhirVersion"] = metadata.get("fhirVersion")
        except Exception as error:
            result["metadataAvailable"] = False
            result["metadataError"] = str(error)

        return result

    return {
        "provider": provider,
        "ok": False,
        "error": "Unsupported provider.",
    }