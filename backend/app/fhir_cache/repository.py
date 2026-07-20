from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import settings

try:
    from pymongo import ASCENDING, MongoClient
    from pymongo.collection import Collection
    from pymongo.errors import PyMongoError
except ImportError:
    ASCENDING = 1
    MongoClient = None
    Collection = Any

    class PyMongoError(Exception):
        pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MongoFhirSnapshotRepository:
    """Optional MongoDB repository that never breaks the existing Oracle path."""

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "MONGODB_ENABLED", False))
        self.uri = str(
            getattr(settings, "MONGODB_URI", "mongodb://127.0.0.1:27017")
        ).strip()
        self.database_name = str(
            getattr(settings, "MONGODB_DATABASE", "kardiogenics")
        ).strip()
        self.collection_name = str(
            getattr(
                settings,
                "MONGODB_FHIR_COLLECTION",
                "fhir_patient_snapshots",
            )
        ).strip()
        self.server_selection_timeout_ms = int(
            getattr(settings, "MONGODB_SERVER_SELECTION_TIMEOUT_MS", 1500)
        )

        self._client: Any = None
        self._collection: Collection | None = None
        self._connect_lock = asyncio.Lock()
        self._last_error: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.enabled
            and MongoClient is not None
            and self._collection is not None
        )

    async def initialize(self) -> bool:
        if not self.enabled:
            return False

        if MongoClient is None:
            self._last_error = "pymongo is not installed."
            return False

        if self._collection is not None:
            return True

        async with self._connect_lock:
            if self._collection is not None:
                return True

            try:
                await asyncio.to_thread(self._initialize_sync)
                self._last_error = None
                return True
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                self._client = None
                self._collection = None
                return False

    def _initialize_sync(self) -> None:
        if MongoClient is None:
            raise RuntimeError("pymongo is not installed.")

        client = MongoClient(
            self.uri,
            serverSelectionTimeoutMS=self.server_selection_timeout_ms,
            connectTimeoutMS=self.server_selection_timeout_ms,
            socketTimeoutMS=5000,
            appname="KardioGenics-FHIR-Cache",
        )
        client.admin.command("ping")

        collection = client[self.database_name][self.collection_name]
        collection.create_index(
            [
                ("provider", ASCENDING),
                ("patientId", ASCENDING),
                ("fhirBaseUrl", ASCENDING),
            ],
            unique=True,
            name="uq_provider_patient_fhir_base",
        )
        collection.create_index(
            [("checkedAt", ASCENDING)],
            name="ix_checked_at",
        )

        self._client = client
        self._collection = collection

    async def health(self) -> dict[str, Any]:
        initialized = await self.initialize()
        return {
            "enabled": self.enabled,
            "available": initialized,
            "database": self.database_name,
            "collection": self.collection_name,
            "lastError": self._last_error,
        }

    async def get_snapshot(
        self,
        *,
        provider: str,
        patient_id: str,
        fhir_base_url: str,
    ) -> dict[str, Any] | None:
        if not await self.initialize():
            return None

        assert self._collection is not None

        try:
            return await asyncio.to_thread(
                self._collection.find_one,
                {
                    "provider": provider,
                    "patientId": patient_id,
                    "fhirBaseUrl": fhir_base_url,
                },
                {"_id": 0},
            )
        except PyMongoError as error:
            self._last_error = f"{type(error).__name__}: {error}"
            return None

    async def save_snapshot(
        self,
        *,
        provider: str,
        patient_id: str,
        fhir_base_url: str,
        snapshot: dict[str, Any],
        fingerprint: str,
    ) -> dict[str, Any]:
        if not await self.initialize():
            return {
                "stored": False,
                "changed": False,
                "reason": self._last_error or "mongodb_unavailable",
            }

        assert self._collection is not None

        query = {
            "provider": provider,
            "patientId": patient_id,
            "fhirBaseUrl": fhir_base_url,
        }
        now = utc_now()

        try:
            previous = await asyncio.to_thread(
                self._collection.find_one,
                query,
                {
                    "_id": 0,
                    "fingerprint": 1,
                    "version": 1,
                },
            )

            changed = (
                previous is None
                or previous.get("fingerprint") != fingerprint
            )

            if changed:
                version = int((previous or {}).get("version", 0)) + 1
                update = {
                    "$set": {
                        **query,
                        "snapshot": snapshot,
                        "fingerprint": fingerprint,
                        "version": version,
                        "fetchedAt": now,
                        "checkedAt": now,
                        "lastChangedAt": now,
                        "updatedAt": now,
                    },
                    "$setOnInsert": {"createdAt": now},
                }
            else:
                version = int((previous or {}).get("version", 1))
                # Clinical data is not rewritten when the fingerprint is unchanged.
                update = {
                    "$set": {
                        "checkedAt": now,
                        "updatedAt": now,
                    }
                }

            await asyncio.to_thread(
                self._collection.update_one,
                query,
                update,
                upsert=True,
            )

            document = await asyncio.to_thread(
                self._collection.find_one,
                query,
                {"_id": 0},
            )

            return {
                "stored": True,
                "changed": changed,
                "version": version,
                "document": document,
            }
        except PyMongoError as error:
            self._last_error = f"{type(error).__name__}: {error}"
            return {
                "stored": False,
                "changed": False,
                "reason": self._last_error,
            }


mongo_fhir_snapshot_repository = MongoFhirSnapshotRepository()
