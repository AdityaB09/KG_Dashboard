from __future__ import annotations

import argparse
import ast
from pathlib import Path


def contains(path: Path, *tokens: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return all(token in text for token in tokens)


def parse_python(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        return True
    except SyntaxError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    backend = root / "backend"
    src = root / "src"

    checks: list[tuple[bool, str]] = []

    service = backend / "app/escalation/epic/cds_service.py"
    routes = backend / "app/escalation/routes.py"
    cards = backend / "app/escalation/epic/cds_cards.py"
    clinical = src / "components/ClinicalPhysiologyPage.jsx"
    clinical_css = src / "components/ClinicalPhysiologyPage.css"
    status_card = src / "components/EscalationStatusCard.jsx"
    status_css = src / "components/EscalationStatusCard.css"

    checks.append((parse_python(service), "Epic CDS service helper parses"))
    checks.append((parse_python(routes), "Escalation routes parse"))
    checks.append((parse_python(cards), "Epic CDS card builder parses"))
    checks.append((contains(routes,
        '/api/integrations/epic/cds-hooks/cds-services',
        'epic_cds_standard_service',
        'epic_cds_standard_feedback',
        'EPIC_CDS_HOOK_INVOKED',
        'EPIC_CDS_CARD_RETURNED',
    ), "Standard + compatibility Epic CDS routes are installed"))
    checks.append((contains(service,
        'DEFAULT_HOOK = "patient-view"',
        'standardServiceUrl',
        'standardFeedbackUrl',
    ), "Epic discovery/URL contract is installed"))
    checks.append((contains(cards,
        'EPIC_CDS_LINK_TYPE',
        '"absolute"',
        'Open CARDINAL Escalation',
    ), "CDS card deep link defaults to an absolute CARDINAL link"))
    checks.append((contains(clinical,
        'kgen-critical-interpretation-panel',
        'kgen-critical-interpretation-box',
    ), "ClinicalPhysiologyPage has isolated Critical Interpretation scope"))
    checks.append((contains(clinical_css,
        'CARDINAL EPIC/UI V8',
        '.kgen-critical-interpretation-panel .kgen-slm-compact-card',
        '.kgen-critical-interpretation-panel .kgen-slm-compact-mechanism',
    ), "Critical Interpretation layout polish is scoped to the analytics widget"))
    checks.append((contains(status_card,
        'role="link"',
        'tabIndex={0}',
        'onClick={openEscalation}',
        'onKeyDown={onStripKeyDown}',
    ), "Entire escalation response strip is clickable and keyboard accessible"))
    checks.append((contains(status_css,
        '.cardinal-response-strip:focus-visible',
        'cursor: pointer',
    ), "Escalation response strip has click/focus affordance"))

    protected = [backend / ".env", src / ".env"]
    checks.append((all(path.exists() for path in protected), "Existing frontend/backend .env files are still present"))

    failures = 0
    for ok, label in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures += 1

    print()
    print(f"FAIL={failures}")
    print("RESULT=PASS" if failures == 0 else "RESULT=FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
