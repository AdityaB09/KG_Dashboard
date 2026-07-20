from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

INCIDENT_SPECIFIC_KEYS = {
    "incidentId",
    "storedWithEpisodeId",
    "contextAnchor",
    "clinicalCache",
}

RELATIVE_TIME_KEYS = {
    "minutesFromAnchor",
    "relation",
    "relationLabel",
    "temporalBucket",
    "latestRelation",
    "latestRelationLabel",
}

TRANSIENT_PROVENANCE_KEYS = {
    "loadedAt",
    "checkedAt",
    "cacheCheckedAt",
}

_SORT_ID_KEYS = (
    "resourceId",
    "sourceId",
    "id",
    "field",
    "name",
    "eventTime",
    "observedAt",
    "effectiveAt",
    "date",
)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def temporal_bucket(minutes_from_anchor: float | None) -> str:
    if minutes_from_anchor is None:
        return "unknown"

    absolute_minutes = abs(minutes_from_anchor)

    if absolute_minutes <= 60:
        return "episode_near"
    if absolute_minutes <= 24 * 60:
        return "within_one_day"
    if absolute_minutes <= 7 * 24 * 60:
        return "recent"
    if absolute_minutes <= 90 * 24 * 60:
        return "historical"
    return "historical_remote"


def relation_to_anchor(
    value: datetime | None,
    anchor: datetime,
) -> dict[str, Any]:
    if value is None:
        return {
            "minutesFromAnchor": None,
            "relation": "unknown",
            "relationLabel": "Time unavailable",
            "temporalBucket": "unknown",
        }

    minutes = round(
        (value - anchor).total_seconds() / 60.0,
        1,
    )

    if abs(minutes) < 1:
        relation = "at_anchor"
        label = "At context snapshot"
    elif minutes < 0:
        relation = "before_anchor"
        label = f"{abs(minutes):g} min before"
    else:
        relation = "after_anchor"
        label = f"{minutes:g} min after"

    return {
        "minutesFromAnchor": minutes,
        "relation": relation,
        "relationLabel": label,
        "temporalBucket": temporal_bucket(minutes),
    }


def _strip_relative_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_relative_fields(item) for item in value]

    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}

    for key, item in value.items():
        if key in INCIDENT_SPECIFIC_KEYS or key in RELATIVE_TIME_KEYS:
            continue

        if key == "provenance" and isinstance(item, dict):
            cleaned[key] = {
                provenance_key: _strip_relative_fields(provenance_value)
                for provenance_key, provenance_value in item.items()
                if provenance_key not in TRANSIENT_PROVENANCE_KEYS
            }
            continue

        if key == "sourceResolution" and isinstance(item, dict):
            source_resolution = copy.deepcopy(item)
            source_resolution.pop("session_count", None)
            source_resolution.pop("sessionCount", None)
            source_resolution.pop("limitation", None)
            cleaned[key] = _strip_relative_fields(source_resolution)
            continue

        cleaned[key] = _strip_relative_fields(item)

    return cleaned


def _stable_sort_key(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return (json.dumps(value, sort_keys=True, default=str),)

    parts = [str(value.get(key) or "") for key in _SORT_ID_KEYS]
    parts.append(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    return tuple(parts)


def _stable_normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_normalize(value[key])
            for key in sorted(value)
        }

    if isinstance(value, list):
        normalized = [_stable_normalize(item) for item in value]
        if all(isinstance(item, dict) for item in normalized):
            return sorted(normalized, key=_stable_sort_key)
        return normalized

    return value


def canonical_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    """Convert incident-relative context into a reusable patient snapshot."""
    stripped = _strip_relative_fields(copy.deepcopy(context))
    stripped["schemaVersion"] = (
        stripped.get("schemaVersion") or "clinical-context-v1"
    )
    return _stable_normalize(stripped)


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        _stable_normalize(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rebase_timed_items(
    items: list[dict[str, Any]],
    *,
    time_key: str,
    anchor: datetime,
) -> list[dict[str, Any]]:
    output = []
    for source_item in items:
        item = copy.deepcopy(source_item)
        item.update(relation_to_anchor(parse_datetime(item.get(time_key)), anchor))
        output.append(item)
    return output


def _rebase_trends(
    trends: list[dict[str, Any]],
    *,
    anchor: datetime,
) -> list[dict[str, Any]]:
    output = []

    for source_trend in trends:
        trend = copy.deepcopy(source_trend)
        points = _rebase_timed_items(
            trend.get("points", []) or [],
            time_key="observedAt",
            anchor=anchor,
        )
        points.sort(
            key=lambda point: (
                parse_datetime(point.get("observedAt")).timestamp()
                if parse_datetime(point.get("observedAt"))
                else float("-inf")
            )
        )
        trend["points"] = points

        if points:
            latest = points[-1]
            trend["latestValue"] = latest.get("value")
            trend["latestAt"] = latest.get("observedAt")
            trend["unit"] = latest.get("unit") or trend.get("unit")
            trend["latestRelation"] = latest.get("relation")
            trend["latestRelationLabel"] = latest.get("relationLabel")
            trend["temporalBucket"] = latest.get("temporalBucket")
        else:
            relation = relation_to_anchor(
                parse_datetime(trend.get("latestAt")),
                anchor,
            )
            trend["latestRelation"] = relation["relation"]
            trend["latestRelationLabel"] = relation["relationLabel"]
            trend["temporalBucket"] = relation["temporalBucket"]

        output.append(trend)

    return output


def rebase_snapshot_for_incident(
    snapshot: dict[str, Any],
    *,
    incident_id: str,
    stored_with_episode_id: str | None,
    anchor: datetime,
    anchor_basis: str,
    cache_metadata: dict[str, Any],
) -> dict[str, Any]:
    context = copy.deepcopy(snapshot)
    context["incidentId"] = incident_id
    context["storedWithEpisodeId"] = stored_with_episode_id
    context["contextAnchor"] = {
        "value": anchor.isoformat(),
        "basis": anchor_basis,
    }

    context["labTrends"] = _rebase_trends(
        context.get("labTrends", []) or [],
        anchor=anchor,
    )
    context["vitalTrends"] = _rebase_trends(
        context.get("vitalTrends", []) or [],
        anchor=anchor,
    )
    context["medicationTimeline"] = _rebase_timed_items(
        context.get("medicationTimeline", []) or [],
        time_key="eventTime",
        anchor=anchor,
    )

    context["clinicalCache"] = copy.deepcopy(cache_metadata)

    source_resolution = context.setdefault("sourceResolution", {})
    previous_source = source_resolution.get("source")
    source_resolution.update(
        {
            "source": "mongodb_cache",
            "underlyingSource": previous_source,
            "cacheFingerprint": cache_metadata.get("fingerprint"),
        }
    )

    data_quality = context.setdefault("dataQuality", {})
    data_quality["cacheUsed"] = True
    data_quality["cacheStale"] = bool(cache_metadata.get("stale"))

    limitations = list(context.get("limitations", []) or [])
    if cache_metadata.get("stale"):
        message = (
            "Oracle/FHIR clinical context was served from the MongoDB cache "
            "while a background refresh was scheduled."
        )
        if message not in limitations:
            limitations.append(message)
    context["limitations"] = limitations

    return context
