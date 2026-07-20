from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.clinical_context import clinical_context_service
from app.config import settings
from app.fhir_cache.repository import mongo_fhir_snapshot_repository
from app.fhir_cache.snapshot import (
    canonical_snapshot,
    parse_datetime,
    rebase_snapshot_for_incident,
    snapshot_fingerprint,
)
from app.incidents import incident_coordinator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value) if value else None


class FhirCacheService:
    def __init__(self) -> None:
        self.repository = mongo_fhir_snapshot_repository
        self.soft_refresh_seconds = int(
            getattr(settings, "FHIR_CACHE_SOFT_REFRESH_SECONDS", 300)
        )
        self.max_stale_seconds = int(
            getattr(settings, "FHIR_CACHE_MAX_STALE_SECONDS", 86400)
        )
        self.background_refresh_enabled = bool(
            getattr(settings, "FHIR_CACHE_BACKGROUND_REFRESH", True)
        )
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: set[asyncio.Task[Any]] = set()

    def _key(self, *, patient_id: str, fhir_base_url: str) -> str:
        return f"{patient_id}|{fhir_base_url}"

    def _lock(
        self,
        *,
        patient_id: str,
        fhir_base_url: str,
    ) -> asyncio.Lock:
        key = self._key(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )
        if key not in self._refresh_locks:
            self._refresh_locks[key] = asyncio.Lock()
        return self._refresh_locks[key]

    def _document_age_seconds(
        self,
        document: dict[str, Any],
    ) -> float | None:
        checked = parse_datetime(
            document.get("checkedAt") or document.get("fetchedAt")
        )
        if checked is None:
            return None
        return max(0.0, (utc_now() - checked).total_seconds())

    def _incident_anchor(
        self,
        incident_id: str,
    ) -> tuple[dict[str, Any], str | None, datetime, str]:
        incident = incident_coordinator.get_incident(incident_id)
        episodes = incident_coordinator.get_incident_episodes(incident_id)
        stored_with_episode_id = (
            incident.get("bestContextEpisodeId")
            or incident.get("primaryEpisodeId")
        )
        anchor, anchor_basis = clinical_context_service.anchor(
            incident,
            episodes,
        )
        return (
            incident,
            stored_with_episode_id,
            anchor,
            anchor_basis,
        )

    def _cache_metadata(
        self,
        *,
        document: dict[str, Any],
        age_seconds: float | None,
        refresh_scheduled: bool,
    ) -> dict[str, Any]:
        stale = (
            age_seconds is None
            or age_seconds >= self.soft_refresh_seconds
        )
        return {
            "status": "hit",
            "source": "mongodb",
            "fingerprint": document.get("fingerprint"),
            "version": document.get("version"),
            "fetchedAt": _iso(document.get("fetchedAt")),
            "checkedAt": _iso(document.get("checkedAt")),
            "ageSeconds": (
                round(age_seconds, 1)
                if age_seconds is not None
                else None
            ),
            "stale": stale,
            "staleBeyondPreferredWindow": (
                age_seconds is not None
                and age_seconds >= self.max_stale_seconds
            ),
            "refreshScheduled": refresh_scheduled,
        }

    def _context_from_document(
        self,
        *,
        incident_id: str,
        document: dict[str, Any],
        refresh_scheduled: bool,
    ) -> dict[str, Any]:
        (
            _incident,
            stored_with_episode_id,
            anchor,
            anchor_basis,
        ) = self._incident_anchor(incident_id)

        age_seconds = self._document_age_seconds(document)

        context = rebase_snapshot_for_incident(
            document.get("snapshot") or {},
            incident_id=incident_id,
            stored_with_episode_id=stored_with_episode_id,
            anchor=anchor,
            anchor_basis=anchor_basis,
            cache_metadata=self._cache_metadata(
                document=document,
                age_seconds=age_seconds,
                refresh_scheduled=refresh_scheduled,
            ),
        )

        clinical_context_service.save(incident_id, context)
        return context

    async def health(self) -> dict[str, Any]:
        repository_health = await self.repository.health()
        return {
            **repository_health,
            "softRefreshSeconds": self.soft_refresh_seconds,
            "maxStaleSeconds": self.max_stale_seconds,
            "backgroundRefresh": self.background_refresh_enabled,
            "activeRefreshCount": len(
                [task for task in self._refresh_tasks if not task.done()]
            ),
        }

    async def probe(
        self,
        *,
        patient_id: str | None,
        fhir_base_url: str | None,
    ) -> dict[str, Any]:
        if not patient_id or not fhir_base_url:
            return {
                "enabled": self.repository.enabled,
                "available": False,
                "cacheHit": False,
                "reason": "patient_or_fhir_base_missing",
            }

        document = await self.repository.get_snapshot(
            provider="oracle",
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )
        age_seconds = (
            self._document_age_seconds(document)
            if document
            else None
        )

        return {
            "enabled": self.repository.enabled,
            "available": self.repository.available,
            "cacheHit": bool(document),
            "fingerprint": (
                document.get("fingerprint")
                if document
                else None
            ),
            "version": (
                document.get("version")
                if document
                else None
            ),
            "ageSeconds": (
                round(age_seconds, 1)
                if age_seconds is not None
                else None
            ),
        }

    async def store_existing_context(
        self,
        *,
        context: dict[str, Any],
        patient_id: str | None,
        fhir_base_url: str | None,
    ) -> dict[str, Any]:
        if (
            not patient_id
            or not fhir_base_url
            or context.get("status") != "ready"
        ):
            return {
                "stored": False,
                "changed": False,
                "reason": "ready_context_or_source_missing",
            }

        snapshot = canonical_snapshot(context)
        fingerprint = snapshot_fingerprint(snapshot)

        return await self.repository.save_snapshot(
            provider="oracle",
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
            snapshot=snapshot,
            fingerprint=fingerprint,
        )

    def _schedule_background_refresh(
        self,
        *,
        incident_id: str,
        patient_id: str,
        access_token: str,
        fhir_base_url: str,
    ) -> bool:
        if not self.background_refresh_enabled:
            return False

        key = self._key(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )
        task_name = f"fhir-refresh:{key}"

        for task in self._refresh_tasks:
            if not task.done() and task.get_name() == task_name:
                return False

        task = asyncio.create_task(
            self._refresh_and_publish(
                incident_id=incident_id,
                patient_id=patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            ),
            name=task_name,
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)
        return True

    async def _refresh_and_publish(
        self,
        *,
        incident_id: str,
        patient_id: str,
        access_token: str,
        fhir_base_url: str,
    ) -> None:
        lock = self._lock(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )

        async with lock:
            try:
                context = await clinical_context_service.load(
                    incident_id=incident_id,
                    patient_id=patient_id,
                    access_token=access_token,
                    fhir_base_url=fhir_base_url,
                )
                result = await self.store_existing_context(
                    context=context,
                    patient_id=patient_id,
                    fhir_base_url=fhir_base_url,
                )

                event = {
                    "type": (
                        "clinical.context.updated"
                        if result.get("changed")
                        else "clinical.context.checked"
                    ),
                    "incidentId": incident_id,
                    "patientId": patient_id,
                    "changed": bool(result.get("changed")),
                    "cacheVersion": result.get("version"),
                    "createdAt": utc_now().isoformat(),
                }

                try:
                    from app.episodes import episode_coordinator

                    episode_coordinator.publish(event)
                except Exception as publish_error:
                    print(
                        "[KGEN FHIR CACHE EVENT ERROR]",
                        type(publish_error).__name__,
                        str(publish_error),
                    )

                if result.get("changed"):
                    try:
                        from app.phase7.orchestrator import phase7_orchestrator

                        incident = incident_coordinator.get_incident(incident_id)
                        episode_id = (
                            incident.get("primaryEpisodeId")
                            or incident.get("bestContextEpisodeId")
                        )
                        if episode_id:
                            phase7_orchestrator.schedule_captured_episode(
                                episode_id=episode_id,
                                incident_id=incident_id,
                            )
                    except Exception as phase7_error:
                        print(
                            "[KGEN FHIR CACHE PHASE7 ERROR]",
                            type(phase7_error).__name__,
                            str(phase7_error),
                        )
            except Exception as error:
                print(
                    "[KGEN FHIR CACHE REFRESH ERROR]",
                    type(error).__name__,
                    str(error),
                )

    async def get_or_load(
        self,
        *,
        incident_id: str,
        patient_id: str | None,
        access_token: str | None,
        fhir_base_url: str | None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        Return cached FHIR context immediately and revalidate it in the background.

        MongoDB remains optional. When it is disabled or unavailable, the
        existing clinical-context loader is called exactly as before.
        """
        if (
            not patient_id
            or not fhir_base_url
            or not self.repository.enabled
        ):
            return await clinical_context_service.load(
                incident_id=incident_id,
                patient_id=patient_id,
                access_token=access_token,
                fhir_base_url=fhir_base_url,
            )

        document = await self.repository.get_snapshot(
            provider="oracle",
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )

        if document and not force_refresh:
            age_seconds = self._document_age_seconds(document)
            needs_refresh = (
                age_seconds is None
                or age_seconds >= self.soft_refresh_seconds
            )
            refresh_scheduled = False

            if needs_refresh and access_token:
                refresh_scheduled = self._schedule_background_refresh(
                    incident_id=incident_id,
                    patient_id=patient_id,
                    access_token=access_token,
                    fhir_base_url=fhir_base_url,
                )

            return self._context_from_document(
                incident_id=incident_id,
                document=document,
                refresh_scheduled=refresh_scheduled,
            )

        lock = self._lock(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url,
        )

        async with lock:
            if not force_refresh:
                document = await self.repository.get_snapshot(
                    provider="oracle",
                    patient_id=patient_id,
                    fhir_base_url=fhir_base_url,
                )
                if document:
                    return self._context_from_document(
                        incident_id=incident_id,
                        document=document,
                        refresh_scheduled=False,
                    )

            try:
                context = await clinical_context_service.load(
                    incident_id=incident_id,
                    patient_id=patient_id,
                    access_token=access_token,
                    fhir_base_url=fhir_base_url,
                )
                result = await self.store_existing_context(
                    context=context,
                    patient_id=patient_id,
                    fhir_base_url=fhir_base_url,
                )

                if result.get("stored"):
                    context.setdefault("clinicalCache", {}).update(
                        {
                            "status": "miss_loaded",
                            "source": "oracle",
                            "fingerprint": (
                                result.get("document", {}).get("fingerprint")
                            ),
                            "version": result.get("version"),
                            "stale": False,
                            "refreshScheduled": False,
                        }
                    )

                return context
            except Exception:
                # A failed refresh may fall back to the previous snapshot.
                if document:
                    context = self._context_from_document(
                        incident_id=incident_id,
                        document=document,
                        refresh_scheduled=False,
                    )
                    context.setdefault("limitations", []).append(
                        "Oracle refresh failed; the previously cached "
                        "FHIR context is being used."
                    )
                    clinical_context_service.save(incident_id, context)
                    return context
                raise


fhir_cache_service = FhirCacheService()
