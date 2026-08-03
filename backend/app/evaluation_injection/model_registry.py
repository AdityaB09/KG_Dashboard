from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ModelRegistryError(RuntimeError):
    pass


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    return _backend_root() / "config" / "slm_models.json"


def load_model_registry() -> dict[str, Any]:
    path = registry_path()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelRegistryError(f"Model registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ModelRegistryError(f"Invalid JSON in model registry: {exc}") from exc

    models = payload.get("models")
    if not isinstance(models, list):
        raise ModelRegistryError("Model registry must contain a models array.")

    seen_ids: set[str] = set()
    seen_ollama_names: set[str] = set()

    for model in models:
        if not isinstance(model, dict):
            raise ModelRegistryError("Each model registry item must be an object.")

        model_id = str(model.get("id") or "").strip()
        ollama_name = str(model.get("ollamaName") or "").strip()

        if not model_id or not ollama_name:
            raise ModelRegistryError("Every model requires id and ollamaName.")

        if model_id in seen_ids:
            raise ModelRegistryError(f"Duplicate model id: {model_id}")

        if ollama_name in seen_ollama_names:
            raise ModelRegistryError(f"Duplicate Ollama name: {ollama_name}")

        seen_ids.add(model_id)
        seen_ollama_names.add(ollama_name)

    return payload


def list_models(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    models = load_model_registry()["models"]

    if enabled_only:
        models = [model for model in models if model.get("enabled", True)]

    return models


def resolve_model(identifier: str) -> dict[str, Any]:
    identifier = str(identifier or "").strip()

    if not identifier:
        raise ModelRegistryError("Model identifier cannot be empty.")

    for model in list_models(enabled_only=False):
        if identifier in {
            str(model.get("id") or ""),
            str(model.get("ollamaName") or ""),
            str(model.get("displayName") or ""),
        }:
            return model

    # Direct Ollama names are allowed even before they are added to the registry.
    return {
        "id": identifier,
        "displayName": identifier,
        "ollamaName": identifier,
        "enabled": True,
        "registryFallback": True,
    }
