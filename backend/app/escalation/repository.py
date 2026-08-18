from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from app.config import settings
from app.escalation.models import now_iso


class EscalationRepository:
    def __init__(self) -> None:
        self._lock = RLock()

    @staticmethod
    def _root() -> Path:
        configured = Path(settings.ESCALATION_STORAGE_PATH)
        if not configured.is_absolute():
            configured = Path(__file__).resolve().parents[2] / configured
        configured.mkdir(parents=True, exist_ok=True)
        return configured.resolve()

    def _path(self, event_id: str) -> Path:
        safe = str(event_id or "").strip()
        if not safe.startswith("esc-") or any(ch in safe for ch in ("/", "\\", "..")):
            raise ValueError("Invalid escalation event ID.")
        return self._root() / f"{safe}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def create(self, case: dict[str, Any]) -> dict[str, Any]:
        path = self._path(str(case.get("eventId") or ""))
        with self._lock:
            if path.exists():
                raise FileExistsError(f"Escalation case already exists: {case.get('eventId')}")
            self._atomic_write(path, case)
        return dict(case)

    def get(self, event_id: str) -> dict[str, Any] | None:
        path = self._path(event_id)
        with self._lock:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def update(
        self,
        event_id: str,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        path = self._path(event_id)
        with self._lock:
            if not path.exists():
                raise KeyError(event_id)
            case = json.loads(path.read_text(encoding="utf-8"))
            mutate(case)
            case["updatedAt"] = now_iso()
            self._atomic_write(path, case)
            return case

    def list_cases(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(self._root().glob("esc-*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    items.append(payload)
        items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return items

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        for case in self.list_cases():
            if str(case.get("idempotencyKey") or "") == key:
                return case
            historical = case.get("idempotencyKeys")
            if isinstance(historical, list) and key in {str(value) for value in historical}:
                return case
        return None

    def find_by_episode(self, episode_id: str) -> dict[str, Any] | None:
        for case in self.list_cases():
            if str(case.get("episodeId") or "") == str(episode_id):
                return case
        return None

    def find_by_incident(self, incident_id: str) -> dict[str, Any] | None:
        for case in self.list_cases():
            if str(case.get("incidentId") or "") == str(incident_id):
                return case
        return None

    def find_active(
        self,
        *,
        patient_id: str,
        encounter_id: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any] | None:
        terminal = {"RESOLVED", "CANCELLED"}
        for case in self.list_cases():
            if str(case.get("status") or "").upper() in terminal:
                continue
            if str(case.get("patientId") or "") != str(patient_id or ""):
                continue
            if provider and str(case.get("provider") or "").lower() != provider.lower():
                continue
            case_encounter = str(case.get("encounterId") or "")
            requested_encounter = str(encounter_id or "")
            if requested_encounter and case_encounter and case_encounter != requested_encounter:
                continue
            return case
        return None

    def due_cases(self, now_text: str) -> list[dict[str, Any]]:
        return [
            case
            for case in self.list_cases()
            if bool(case.get("autoEscalationEnabled"))
            and str(case.get("status") or "") in {"ROUTED_AUTO_ADVANCE", "ACK_PENDING"}
            and case.get("nextEscalationAt")
            and str(case["nextEscalationAt"]) <= now_text
        ]


escalation_repository = EscalationRepository()
