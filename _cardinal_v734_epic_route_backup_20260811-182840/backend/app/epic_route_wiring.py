from __future__ import annotations

from fastapi import FastAPI

from app.epic_smart import router as epic_smart_router
from app.evaluation_demo.epic_routes import router as epic_evaluation_demo_router


EPIC_SMART_REQUIRED = {
    "/auth/epic/launch",
    "/auth/epic/callback",
    "/auth/epic/logout",
}

EPIC_EVAL_REQUIRED = {
    "/api/epic-evaluation-demo/bootstrap",
    "/api/epic-evaluation-demo/start",
    "/api/epic-evaluation-demo/mapping-status",
}


def _route_paths(app: FastAPI) -> set[str]:
    return {
        str(getattr(route, "path", ""))
        for route in app.routes
        if getattr(route, "path", None)
    }


def install_epic_routes(app: FastAPI) -> None:
    """Idempotently register Epic SMART + Epic evaluation routes.

    This is deliberately separate from Oracle route wiring.  It exists to make
    deployment failures loud: if a future main.py refactor forgets to include
    the Epic routers, this function adds them and then asserts that the public
    paths are present.
    """

    paths = _route_paths(app)

    if "/auth/epic/launch" not in paths:
        app.include_router(epic_smart_router)
        paths = _route_paths(app)

    if "/api/epic-evaluation-demo/bootstrap" not in paths:
        app.include_router(epic_evaluation_demo_router)
        paths = _route_paths(app)

    required = EPIC_SMART_REQUIRED | EPIC_EVAL_REQUIRED
    missing = sorted(required - paths)
    if missing:
        raise RuntimeError(
            "CARDINAL Epic route registration failed. Missing routes: "
            + ", ".join(missing)
        )

    print(
        "[CARDINAL EPIC ROUTES READY]",
        {
            "smart": sorted(EPIC_SMART_REQUIRED),
            "evaluation": sorted(EPIC_EVAL_REQUIRED),
        },
    )
