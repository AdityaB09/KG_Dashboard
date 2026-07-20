from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable, Mapping


class Phase7IntegrationError(
    RuntimeError
):
    pass


def _call_with_optional_force(
    function: Callable[..., Any],
    identifier: str,
    force: bool,
) -> Any:
    signature = inspect.signature(
        function
    )

    if "force" in signature.parameters:
        return function(
            identifier,
            force=force,
        )

    return function(identifier)


def analyze_episode_sync(
    episode_id: str,
    *,
    force: bool,
) -> dict[str, Any]:
    module = importlib.import_module(
        "app.analysis.episode_analyzer"
    )

    service = getattr(
        module,
        "episode_analyzer",
        None,
    )

    if service is None:
        raise Phase7IntegrationError(
            "app.analysis.episode_analyzer "
            "does not expose episode_analyzer."
        )

    function = getattr(
        service,
        "analyze",
        None,
    )

    if not callable(function):
        raise Phase7IntegrationError(
            "episode_analyzer.analyze is "
            "not available."
        )

    result = _call_with_optional_force(
        function,
        episode_id,
        force,
    )

    if not isinstance(
        result,
        Mapping,
    ):
        raise Phase7IntegrationError(
            "Episode analysis did not "
            "return an object."
        )

    return dict(result)


def analyze_incident_sync(
    incident_id: str,
    *,
    force: bool,
) -> dict[str, Any]:
    module = importlib.import_module(
        "app.analysis.incident_analyzer"
    )

    service = getattr(
        module,
        "incident_analyzer",
        None,
    )

    if service is None:
        raise Phase7IntegrationError(
            "app.analysis.incident_analyzer "
            "does not expose incident_analyzer."
        )

    function = getattr(
        service,
        "analyze",
        None,
    )

    if not callable(function):
        raise Phase7IntegrationError(
            "incident_analyzer.analyze is "
            "not available."
        )

    result = _call_with_optional_force(
        function,
        incident_id,
        force,
    )

    if not isinstance(
        result,
        Mapping,
    ):
        raise Phase7IntegrationError(
            "Incident analysis did not "
            "return an object."
        )

    return dict(result)


def build_slm_context_sync(
    incident_id: str,
) -> dict[str, Any]:
    candidates: list[
        Callable[..., Any]
    ] = []

    try:
        module = importlib.import_module(
            "app.analysis.slm_context"
        )

        for name in (
            "build_incident_slm_context",
            "build_slm_context",
        ):
            function = getattr(
                module,
                name,
                None,
            )

            if callable(function):
                candidates.append(
                    function
                )

        for service_name in (
            "slm_context_builder",
            "slm_context_service",
        ):
            service = getattr(
                module,
                service_name,
                None,
            )

            function = getattr(
                service,
                "build",
                None,
            )

            if callable(function):
                candidates.append(
                    function
                )

    except ModuleNotFoundError:
        pass

    from app.incidents import (
        incident_coordinator,
    )

    coordinator_builder = getattr(
        incident_coordinator,
        "build_slm_context",
        None,
    )

    if callable(
        coordinator_builder
    ):
        candidates.append(
            coordinator_builder
        )

    errors: list[str] = []

    for function in candidates:
        try:
            result = function(
                incident_id
            )

            if isinstance(
                result,
                Mapping,
            ):
                return dict(result)

        except Exception as error:
            errors.append(
                f"{function!r}: "
                f"{type(error).__name__}: "
                f"{error}"
            )

    detail = (
        "; ".join(errors)
        if errors
        else "No compatible builder exists."
    )

    raise Phase7IntegrationError(
        "Unable to build the incident "
        f"SLM context. {detail}"
    )


def get_incident(
    incident_id: str,
) -> dict[str, Any]:
    from app.incidents import (
        incident_coordinator,
    )

    return dict(
        incident_coordinator
        .get_incident(
            incident_id
        )
    )


def get_incident_episodes(
    incident_id: str,
) -> list[dict[str, Any]]:
    from app.incidents import (
        incident_coordinator,
    )

    return [
        dict(item)
        for item in (
            incident_coordinator
            .get_incident_episodes(
                incident_id
            )
        )
    ]


def get_episode(
    episode_id: str,
) -> dict[str, Any]:
    from app.episodes import (
        episode_coordinator,
    )

    return dict(
        episode_coordinator
        .get_episode(
            episode_id
        )
    )


def publish_event(
    event: dict[str, Any],
) -> None:
    try:
        from app.episodes import (
            episode_coordinator,
        )

        publish = getattr(
            episode_coordinator,
            "publish",
            None,
        )

        if callable(publish):
            publish(event)

    except Exception:
        # Event publication must never fail
        # the persisted analysis pipeline.
        return
