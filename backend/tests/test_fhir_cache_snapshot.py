from datetime import datetime, timezone

from app.fhir_cache.snapshot import (
    canonical_snapshot,
    rebase_snapshot_for_incident,
    snapshot_fingerprint,
)


def test_snapshot_removes_incident_relative_fields():
    context = {
        "schemaVersion": "clinical-context-v1",
        "incidentId": "inc-one",
        "storedWithEpisodeId": "ep-one",
        "contextAnchor": {
            "value": "2026-07-16T17:00:00+00:00",
            "basis": "capture_time_proxy",
        },
        "labTrends": [
            {
                "field": "potassium",
                "latestValue": 5.8,
                "latestRelation": "before_anchor",
                "points": [
                    {
                        "resourceId": "obs-1",
                        "value": 5.8,
                        "observedAt": "2026-07-16T16:50:00+00:00",
                        "minutesFromAnchor": -10,
                        "relation": "before_anchor",
                        "temporalBucket": "episode_near",
                    }
                ],
            }
        ],
    }

    snapshot = canonical_snapshot(context)

    assert "incidentId" not in snapshot
    assert "storedWithEpisodeId" not in snapshot
    assert "contextAnchor" not in snapshot

    point = snapshot["labTrends"][0]["points"][0]
    assert "minutesFromAnchor" not in point
    assert "relation" not in point
    assert "temporalBucket" not in point


def test_snapshot_fingerprint_ignores_incident_anchor():
    base = {
        "schemaVersion": "clinical-context-v1",
        "patientSummary": {"gender": "male"},
        "labTrends": [],
    }

    first = {
        **base,
        "incidentId": "inc-one",
        "contextAnchor": {
            "value": "2026-07-16T17:00:00+00:00"
        },
    }
    second = {
        **base,
        "incidentId": "inc-two",
        "contextAnchor": {
            "value": "2026-07-17T17:00:00+00:00"
        },
    }

    assert snapshot_fingerprint(
        canonical_snapshot(first)
    ) == snapshot_fingerprint(
        canonical_snapshot(second)
    )


def test_cached_snapshot_is_rebased_for_new_incident():
    snapshot = {
        "schemaVersion": "clinical-context-v1",
        "status": "ready",
        "labTrends": [
            {
                "field": "glucose",
                "points": [
                    {
                        "resourceId": "obs-1",
                        "value": 100,
                        "unit": "mg/dL",
                        "observedAt": "2026-07-16T16:00:00+00:00",
                    }
                ],
            }
        ],
        "vitalTrends": [],
        "medicationTimeline": [],
    }

    context = rebase_snapshot_for_incident(
        snapshot,
        incident_id="inc-two",
        stored_with_episode_id="ep-two",
        anchor=datetime(
            2026,
            7,
            16,
            17,
            0,
            tzinfo=timezone.utc,
        ),
        anchor_basis="capture_time_proxy",
        cache_metadata={
            "status": "hit",
            "source": "mongodb",
            "fingerprint": "abc",
            "stale": False,
            "refreshScheduled": False,
        },
    )

    point = context["labTrends"][0]["points"][0]

    assert context["incidentId"] == "inc-two"
    assert point["minutesFromAnchor"] == -60.0
    assert point["temporalBucket"] == "episode_near"
