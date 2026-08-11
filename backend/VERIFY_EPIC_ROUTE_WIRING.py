from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

print("Backend root:", ROOT)

# Inspect source routers before main import; this immediately tells us whether
# the endpoint modules themselves contain their expected APIRouter routes.
from app.epic_smart import router as epic_smart_router
from app.evaluation_demo.epic_routes import router as epic_eval_router

print("Epic SMART APIRouter route count:", len(epic_smart_router.routes))
for route in epic_smart_router.routes:
    print("  source-smart:", sorted(getattr(route, "methods", set()) or set()), getattr(route, "path", None))

print("Epic evaluation APIRouter route count:", len(epic_eval_router.routes))
for route in epic_eval_router.routes:
    print("  source-eval:", sorted(getattr(route, "methods", set()) or set()), getattr(route, "path", None))

module = importlib.import_module("main")
app = module.app

required = {
    ("HEAD", "/auth/epic/launch"),
    ("GET", "/auth/epic/launch"),
    ("GET", "/auth/epic/callback"),
    ("GET", "/auth/epic/logout"),
    ("GET", "/api/epic-evaluation-demo/bootstrap"),
    ("POST", "/api/epic-evaluation-demo/start"),
    ("GET", "/api/epic-evaluation-demo/mapping-status"),
}

actual: set[tuple[str, str]] = set()
for route in app.routes:
    path = getattr(route, "path", None)
    if not path:
        continue
    for method in getattr(route, "methods", None) or set():
        actual.add((str(method).upper(), str(path)))

missing = sorted(required - actual)
if missing:
    print("CARDINAL Epic route wiring verification: FAIL")
    for method, path in missing:
        print(" - missing", method, path)
    raise SystemExit(1)

print("CARDINAL Epic route wiring verification: PASS")
for method, path in sorted(required, key=lambda item: (item[1], item[0])):
    print(" -", method, path)
