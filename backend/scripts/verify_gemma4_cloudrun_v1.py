from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import sys
import urllib.error
import urllib.request
from pathlib import Path

TARGET_SLM_SHA256 = "0262187f0a61f271b7c4e5a02505328539c81de46fd3b757c65fa4afc6085fde"


def read_env_last(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--runtime", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    backend = root / "backend"
    env_path = backend / ".env"
    slm_path = backend / "app" / "evaluation" / "slm_client.py"
    config_path = backend / "app" / "evaluation" / "config.py"

    passed = 0
    warned = 0
    failed = 0

    def check(ok: bool, label: str, warn: bool = False) -> None:
        nonlocal passed, warned, failed
        if ok:
            passed += 1
            print(f"[PASS] {label}")
        elif warn:
            warned += 1
            print(f"[WARN] {label}")
        else:
            failed += 1
            print(f"[FAIL] {label}")

    print("=== CARDINAL GEMMA 4 CLOUD RUN SWITCH V1 VERIFIER ===")

    check(env_path.exists(), "backend/.env exists")
    check(slm_path.exists(), "backend/app/evaluation/slm_client.py exists")
    check(config_path.exists(), "backend/app/evaluation/config.py exists")
    if not (env_path.exists() and slm_path.exists() and config_path.exists()):
        print(f"\nPASS={passed} WARN={warned} FAIL={failed}")
        print("RESULT=FAIL")
        return 1

    env = read_env_last(env_path)
    expected = {
        "CARDINAL_LLM_PROVIDER": "gemma4",
        "SLM_BASE_URL": "http://127.0.0.1:9090",
        "SLM_CHAT_PATH": "/v1/chat/completions",
        "SLM_MODEL": "gemma4-31b",
        "SLM_API_KEY": "",
        "SLM_AUTH_MODE": "none",
        "SLM_TIMEOUT_SECONDS": "600",
        "SLM_MAX_OUTPUT_TOKENS": "1200",
        "SLM_OMIT_SAMPLING_PARAMS": "false",
        "SLM_REASONING_EFFORT": "",
        "ENABLE_SLM_EVAL": "true",
        "SLM_EVAL_ALLOW_MODEL": "true",
        "EVALUATION_INJECTION_ENABLED": "true",
        "ETIOLOGY_V7_LIVE_MODEL_ENABLED": "true",
        "ETIOLOGY_V7_PRECOMPUTED_ENABLED": "false",
        "ETIOLOGY_V7_PRECOMPUTED_REQUIRED": "false",
    }
    for key, value in expected.items():
        check(env.get(key) == value, f"Effective ENV {key}={value!r}")

    text = slm_path.read_text(encoding="utf-8", errors="replace")
    config_text = config_path.read_text(encoding="utf-8", errors="replace")
    check("CARDINAL_LLM_PROVIDER" in text, "Gemma provider switch exists in slm_client.py")
    check('cardinal_provider == "gemma4"' in text, "Gemma provider branch exists")
    check('"chat_template_kwargs"' in text and '"enable_thinking": False' in text,
          "Gemma chat template disables thinking")
    check('request_payload.pop(\n            "reasoning_effort"' in text,
          "Gemini reasoning_effort is removed for Gemma")
    check('request_payload.pop(\n            "response_format"' in text,
          "Gemma uses the already-validated vLLM request shape")
    check("[CARDINAL-SLM-RAW]" in text, "Raw Gemma response proof log exists")
    check("slm_omit_sampling_params" in config_text, "Current sampling-param compatibility config remains present")
    check("slm_reasoning_effort" in config_text, "Current Gemini compatibility config remains preserved")
    check(sha256(slm_path) == TARGET_SLM_SHA256, "slm_client.py SHA-256 matches Gemma V1 payload")

    try:
        py_compile.compile(str(slm_path), doraise=True)
        check(True, "Gemma slm_client.py compiles")
    except Exception as exc:
        check(False, f"Gemma slm_client.py compiles ({exc})")

    if args.runtime:
        models_url = "http://127.0.0.1:9090/v1/models"
        try:
            with urllib.request.urlopen(models_url, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                check(resp.status == 200 and "gemma4-31b" in body,
                      "Runtime proxy /v1/models exposes gemma4-31b")
        except Exception as exc:
            check(False, f"Runtime proxy /v1/models reachable ({exc})")

        chat_url = "http://127.0.0.1:9090/v1/chat/completions"
        payload = json.dumps({
            "model": "gemma4-31b",
            "messages": [{"role": "user", "content": "Reply exactly: GEMMA_PROXY_RUNTIME_OK"}],
            "temperature": 0,
            "max_tokens": 32,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode("utf-8")
        req = urllib.request.Request(
            chat_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                check(resp.status == 200 and "GEMMA_PROXY_RUNTIME_OK" in body,
                      "Runtime proxy chat completion succeeds")
        except Exception as exc:
            check(False, f"Runtime proxy chat completion succeeds ({exc})")

    print(f"\nPASS={passed} WARN={warned} FAIL={failed}")
    print("RESULT=PASS" if failed == 0 else "RESULT=FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
