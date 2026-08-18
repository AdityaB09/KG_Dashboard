from __future__ import annotations
import argparse, hashlib
from pathlib import Path

EXPECTED = {
  "backend/app/evaluation/slm_client.py": "0262187f0a61f271b7c4e5a02505328539c81de46fd3b757c65fa4afc6085fde",
  "backend/app/phase7/slm_client.py": "0240dfd6db53a118ce0d674460636caf62ec7437081b5f1b0874337b9b41fea0"
}

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one marker, found {count}")
    return text.replace(old, new, 1)

def patch_eval(path: Path):
    text = path.read_text(encoding="utf-8")
    if "from app.cloud_run_auth import apply_slm_auth" in text:
        return False
    text = replace_once(
        text,
        "import httpx\n",
        "import httpx\n\nfrom app.cloud_run_auth import apply_slm_auth\n",
        "evaluation import",
    )
    marker = (
        '    if slm_api_key():\n'
        '        headers["Authorization"] = (\n'
        '            f"Bearer {slm_api_key()}"\n'
        '        )\n'
    )
    replacement = marker + (
        '\n    headers = await apply_slm_auth(\n'
        '        headers,\n'
        '        base_url=slm_base_url(),\n'
        '    )\n'
    )
    text = replace_once(text, marker, replacement, "evaluation auth block")
    path.write_text(text, encoding="utf-8", newline="\n")
    return True

def patch_phase7(path: Path):
    text = path.read_text(encoding="utf-8")
    if "from app.cloud_run_auth import apply_slm_auth" in text:
        return False
    text = replace_once(
        text,
        "import httpx\n",
        "import httpx\n\nfrom app.cloud_run_auth import apply_slm_auth\n",
        "phase7 import",
    )
    marker = (
        '    if phase7_settings.slm_api_key:\n'
        '        headers["Authorization"] = (\n'
        '            "Bearer "\n'
        '            f"{phase7_settings.slm_api_key}"\n'
        '        )\n'
    )
    replacement = marker + (
        '\n    headers = await apply_slm_auth(\n'
        '        headers,\n'
        '        base_url=phase7_settings.slm_base_url,\n'
        '    )\n'
    )
    text = replace_once(text, marker, replacement, "phase7 auth block")
    path.write_text(text, encoding="utf-8", newline="\n")
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    root = Path(args.project_root).resolve()
    targets = [
        root / "backend/app/evaluation/slm_client.py",
        root / "backend/app/phase7/slm_client.py",
    ]
    for p in targets:
        rel = p.relative_to(root).as_posix()
        if not p.exists():
            raise SystemExit(f"Missing patch target: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        already = "from app.cloud_run_auth import apply_slm_auth" in text
        if not already and not args.force and sha256(p) != EXPECTED[rel]:
            raise SystemExit(
                f"Hash guard stopped patch for {rel}. Expected {EXPECTED[rel]}, got {sha256(p)}. "
                "No source files were intentionally overwritten."
            )
    changed = []
    if patch_eval(targets[0]):
        changed.append(targets[0].name)
    if patch_phase7(targets[1]):
        changed.append(targets[1].name)
    print("Cloud Run identity auth patch complete. Changed:", ", ".join(changed) if changed else "already installed")

if __name__ == "__main__":
    main()
