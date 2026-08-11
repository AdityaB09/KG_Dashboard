from __future__ import annotations

from typing import Callable, Iterable

from fastapi import FastAPI

# Import modules, not just router aliases.  This gives us a safe direct-route
# fallback if an environment ends up with an APIRouter that is present but
# whose routes are not copied into the FastAPI app as expected.
from app import epic_smart
from app.evaluation_demo import epic_routes


REQUIRED_METHODS: dict[str, set[str]] = {
    "/auth/epic/launch": {"GET", "HEAD"},
    "/auth/epic/callback": {"GET"},
    "/auth/epic/logout": {"GET"},
    "/api/epic-evaluation-demo/bootstrap": {"GET"},
    "/api/epic-evaluation-demo/start": {"POST"},
    "/api/epic-evaluation-demo/mapping-status": {"GET"},
}


def _registered_methods(app: FastAPI, path: str) -> set[str]:
    methods: set[str] = set()
    for route in app.routes:
        if str(getattr(route, "path", "")) != path:
            continue
        for method in getattr(route, "methods", None) or set():
            methods.add(str(method).upper())
    return methods


def _route_exists(app: FastAPI, path: str, method: str) -> bool:
    return method.upper() in _registered_methods(app, path)


def _add_if_missing(
    app: FastAPI,
    *,
    path: str,
    endpoint: Callable,
    methods: Iterable[str],
    include_in_schema: bool = True,
    name: str | None = None,
) -> None:
    missing = [m.upper() for m in methods if not _route_exists(app, path, m)]
    if not missing:
        return
    app.add_api_route(
        path,
        endpoint,
        methods=missing,
        include_in_schema=include_in_schema,
        name=name,
    )


def _try_router_registration(app: FastAPI) -> None:
    """Use normal FastAPI router registration first.

    main.py may already have included these routers.  include_router is only
    attempted when the corresponding route family is not already visible.
    """
    if not _route_exists(app, "/auth/epic/launch", "GET"):
        router = getattr(epic_smart, "router", None)
        if router is not None and getattr(router, "routes", None):
            app.include_router(router)

    if not _route_exists(app, "/api/epic-evaluation-demo/bootstrap", "GET"):
        router = getattr(epic_routes, "router", None)
        if router is not None and getattr(router, "routes", None):
            app.include_router(router)


def _direct_registration_fallback(app: FastAPI) -> None:
    """Register the exact endpoint callables when normal router wiring failed.

    This does not alter Oracle, waveform, episode, SLM, or frontend routes.
    It only fills missing Epic route+method pairs.
    """
    smart_endpoints = {
        "epic_launch_head": getattr(epic_smart, "epic_launch_head", None),
        "epic_launch": getattr(epic_smart, "epic_launch", None),
        "epic_callback": getattr(epic_smart, "epic_callback", None),
        "epic_logout": getattr(epic_smart, "epic_logout", None),
    }
    eval_endpoints = {
        "bootstrap": getattr(epic_routes, "bootstrap", None),
        "start": getattr(epic_routes, "start", None),
        "status": getattr(epic_routes, "status", None),
        "cancel": getattr(epic_routes, "cancel", None),
        "mapping_status": getattr(epic_routes, "mapping_status", None),
    }

    required_callables = {**smart_endpoints, **eval_endpoints}
    missing_callables = sorted(name for name, fn in required_callables.items() if not callable(fn))
    if missing_callables:
        raise RuntimeError(
            "CARDINAL Epic endpoint module is incomplete. Missing endpoint callables: "
            + ", ".join(missing_callables)
        )

    _add_if_missing(
        app,
        path="/auth/epic/launch",
        endpoint=smart_endpoints["epic_launch_head"],
        methods=["HEAD"],
        include_in_schema=False,
        name="epic_launch_head",
    )
    _add_if_missing(
        app,
        path="/auth/epic/launch",
        endpoint=smart_endpoints["epic_launch"],
        methods=["GET"],
        name="epic_launch",
    )
    _add_if_missing(
        app,
        path="/auth/epic/callback",
        endpoint=smart_endpoints["epic_callback"],
        methods=["GET"],
        name="epic_callback",
    )
    _add_if_missing(
        app,
        path="/auth/epic/logout",
        endpoint=smart_endpoints["epic_logout"],
        methods=["GET"],
        name="epic_logout",
    )

    _add_if_missing(
        app,
        path="/api/epic-evaluation-demo/bootstrap",
        endpoint=eval_endpoints["bootstrap"],
        methods=["GET"],
        name="epic_evaluation_bootstrap",
    )
    _add_if_missing(
        app,
        path="/api/epic-evaluation-demo/start",
        endpoint=eval_endpoints["start"],
        methods=["POST"],
        name="epic_evaluation_start",
    )
    _add_if_missing(
        app,
        path="/api/epic-evaluation-demo/status/{waveform_session_id}",
        endpoint=eval_endpoints["status"],
        methods=["GET"],
        name="epic_evaluation_status",
    )
    _add_if_missing(
        app,
        path="/api/epic-evaluation-demo/cancel/{waveform_session_id}",
        endpoint=eval_endpoints["cancel"],
        methods=["POST"],
        name="epic_evaluation_cancel",
    )
    _add_if_missing(
        app,
        path="/api/epic-evaluation-demo/mapping-status",
        endpoint=eval_endpoints["mapping_status"],
        methods=["GET"],
        name="epic_evaluation_mapping_status",
    )


def _assert_required(app: FastAPI) -> None:
    missing: list[str] = []
    for path, methods in REQUIRED_METHODS.items():
        actual = _registered_methods(app, path)
        for method in methods:
            if method not in actual:
                missing.append(f"{method} {path}")
    if missing:
        smart_router_count = len(getattr(getattr(epic_smart, "router", None), "routes", []) or [])
        eval_router_count = len(getattr(getattr(epic_routes, "router", None), "routes", []) or [])
        raise RuntimeError(
            "CARDINAL Epic route registration failed after normal and direct registration. "
            f"Missing: {', '.join(sorted(missing))}. "
            f"epic_smart.router routes={smart_router_count}; "
            f"epic_routes.router routes={eval_router_count}."
        )


def install_epic_routes(app: FastAPI) -> None:
    """Idempotently guarantee Epic SMART + Epic demo routes on main:app."""
    _try_router_registration(app)
    _direct_registration_fallback(app)
    _assert_required(app)

    print(
        "[CARDINAL EPIC ROUTES READY]",
        {
            path: sorted(_registered_methods(app, path))
            for path in sorted(REQUIRED_METHODS)
        },
    )
