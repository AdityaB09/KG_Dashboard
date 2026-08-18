from __future__ import annotations

import os
from typing import Any

DEFAULT_SERVICE_ID = "cardinal-clinical-escalation-v1"
DEFAULT_HOOK = "patient-view"


def service_id() -> str:
    return os.getenv("EPIC_CDS_SERVICE_ID", DEFAULT_SERVICE_ID).strip() or DEFAULT_SERVICE_ID


def hook_name() -> str:
    return os.getenv("EPIC_CDS_HOOK", DEFAULT_HOOK).strip() or DEFAULT_HOOK


def public_base_url() -> str:
    return os.getenv("EPIC_CDS_PUBLIC_BASE_URL", "").strip().rstrip("/")


def discovery_document() -> dict[str, Any]:
    return {
        "services": [
            {
                "hook": hook_name(),
                "title": "CARDINAL Clinical Escalation",
                "description": (
                    "Returns a CARDINAL escalation card when the Epic patient/encounter "
                    "matches an active CARDINAL escalation."
                ),
                "id": service_id(),
            }
        ]
    }


def public_urls() -> dict[str, str | None]:
    base = public_base_url()
    if not base:
        return {
            "publicBaseUrl": None,
            "discoveryUrl": None,
            "directServiceUrl": None,
            "standardServiceUrl": None,
            "directFeedbackUrl": None,
            "standardFeedbackUrl": None,
        }

    prefix = f"{base}/api/integrations/epic/cds-hooks"
    sid = service_id()
    return {
        "publicBaseUrl": base,
        "discoveryUrl": f"{prefix}/cds-services",
        "directServiceUrl": f"{prefix}/escalation",
        "standardServiceUrl": f"{prefix}/cds-services/{sid}",
        "directFeedbackUrl": f"{prefix}/escalation/feedback",
        "standardFeedbackUrl": f"{prefix}/cds-services/{sid}/feedback",
    }
