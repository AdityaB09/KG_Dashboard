from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

required = {
    "/auth/epic/launch",
    "/auth/epic/callback",
    "/auth/epic/logout",
    "/api/epic-evaluation-demo/bootstrap",
    "/api/epic-evaluation-demo/start",
    "/api/epic-evaluation-demo/mapping-status",
}

errors: list[str] = []

if not MAIN.exists():
    errors.append(f"Missing {MAIN}")
else:
    source = MAIN.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"main.py syntax error: {exc}")

    for token in (
        "from app.epic_route_wiring import install_epic_routes",
        "install_epic_routes(app)",
    ):
        if token not in source:
            errors.append(f"main.py missing route-wiring token: {token}")

for rel in (
    "app/epic_route_wiring.py",
    "app/epic_smart.py",
    "app/epic_sandbox.py",
    "app/evaluation_demo/epic_routes.py",
    "app/evaluation_demo/epic_service.py",
    "app/evaluation_demo/epic_mapping.py",
    "app/evaluation_demo/epic_patient_scenario_map.json",
):
    if not (ROOT / rel).exists():
        errors.append(f"Missing backend/{rel}")

if errors:
    print("CARDINAL Epic route wiring verification: FAIL")
    for error in errors:
        print(" -", error)
    raise SystemExit(1)

# Dynamic verification catches the exact class of failure seen on Render: the
# application imports successfully but the route is not actually registered.
sys.path.insert(0, str(ROOT))
module = importlib.import_module("main")
app = module.app
paths = {getattr(route, "path", None) for route in app.routes}
missing = sorted(required - paths)
if missing:
    print("CARDINAL Epic route wiring verification: FAIL")
    print("Missing runtime routes:")
    for path in missing:
        print(" -", path)
    raise SystemExit(1)

print("CARDINAL Epic route wiring verification: PASS")
for path in sorted(required):
    print(" -", path)
