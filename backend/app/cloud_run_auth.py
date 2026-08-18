from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import subprocess
import shutil
from pathlib import Path
from typing import MutableMapping
from urllib.parse import quote

import httpx

_METADATA_IDENTITY = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)
_CACHE: dict[str, tuple[str, float]] = {}
_LOCK = asyncio.Lock()


def _auth_mode() -> str:
    return os.getenv("SLM_AUTH_MODE", "none").strip().lower() or "none"


def _audience(base_url: str) -> str:
    configured = os.getenv("SLM_AUTH_AUDIENCE", "").strip().rstrip("/")
    if configured:
        return configured
    return base_url.strip().rstrip("/")


def _jwt_exp(token: str) -> float:
    try:
        body = token.split(".", 2)[1]
        body += "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        return float(payload.get("exp") or 0)
    except Exception:
        return 0.0


async def _metadata_identity_token(audience: str) -> str:
    now = time.time()
    cached = _CACHE.get(audience)
    if cached and cached[1] - 300 > now:
        return cached[0]

    async with _LOCK:
        cached = _CACHE.get(audience)
        if cached and cached[1] - 300 > time.time():
            return cached[0]

        url = f"{_METADATA_IDENTITY}?audience={quote(audience, safe='')}&format=full"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers={"Metadata-Flavor": "Google"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "SLM_AUTH_MODE=gcp_identity is enabled, but Cloud Run identity "
                f"token acquisition failed for audience={audience!r}: {exc!r}"
            ) from exc

        token = response.text.strip()
        if not token:
            raise RuntimeError("Cloud Run metadata server returned an empty identity token.")
        exp = _jwt_exp(token) or (time.time() + 3000)
        _CACHE[audience] = (token, exp)
        return token


def _candidate_gcloud_paths() -> list[Path]:
    """Return likely Windows gcloud launchers without assuming PowerShell PATH semantics."""
    candidates: list[Path] = []

    explicit = os.getenv("SLM_GCLOUD_EXECUTABLE", "").strip() or os.getenv(
        "GCLOUD_EXECUTABLE", ""
    ).strip()
    if explicit:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(explicit))))

    # shutil.which works when the Cloud SDK bin directory is in PATH. Prefer the
    # Windows command launcher because subprocess/CreateProcess cannot rely on a
    # PowerShell alias/function named `gcloud`.
    for name in ("gcloud.cmd", "gcloud.exe", "gcloud", "gcloud.ps1"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        sdk_bin = Path(local_app_data) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin"
        candidates.extend(
            [
                sdk_bin / "gcloud.cmd",
                sdk_bin / "gcloud.exe",
                sdk_bin / "gcloud.ps1",
            ]
        )

    program_files_x86 = os.getenv("ProgramFiles(x86)", "").strip()
    if program_files_x86:
        sdk_bin = Path(program_files_x86) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin"
        candidates.extend(
            [
                sdk_bin / "gcloud.cmd",
                sdk_bin / "gcloud.exe",
                sdk_bin / "gcloud.ps1",
            ]
        )

    # De-duplicate while preserving priority.
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _resolve_gcloud_invocation() -> tuple[list[str], str]:
    """Resolve a command that Python can actually spawn on Windows.

    PowerShell may successfully run `gcloud` even when `subprocess.run(["gcloud", ...])`
    raises WinError 2, because PowerShell can resolve .ps1/.cmd wrappers differently.
    This resolver explicitly finds the Cloud SDK launcher and chooses the appropriate
    host process.
    """
    existing: Path | None = None
    attempted: list[str] = []
    for candidate in _candidate_gcloud_paths():
        attempted.append(str(candidate))
        if candidate.exists() and candidate.is_file():
            existing = candidate
            break

    if existing is None:
        hint = "; ".join(attempted[:8]) or "no candidates resolved"
        raise FileNotFoundError(
            "Google Cloud CLI launcher was not found for the Python backend. "
            "PowerShell may still resolve `gcloud`, but Python needs an executable/wrapper path. "
            "Set SLM_GCLOUD_EXECUTABLE to the full path of gcloud.cmd or gcloud.ps1. "
            f"Candidates checked: {hint}"
        )

    suffix = existing.suffix.lower()
    if suffix in {".cmd", ".bat"}:
        comspec = os.getenv("COMSPEC", "").strip() or shutil.which("cmd.exe") or "cmd.exe"
        # IMPORTANT: keep CALL and the batch-file path as separate argv entries.
        # Passing a pre-quoted command string through cmd.exe /S /C causes cmd.exe
        # to strip/ reinterpret the first quote pair. With Cloud SDK installed below
        # "Cloud SDK", that produced: '"C:\\...\\gcloud.cmd" is not recognized'.
        # subprocess.list2cmdline will quote the path-with-spaces correctly, yielding:
        #   cmd.exe /d /c call "C:\...\gcloud.cmd" auth print-identity-token
        return [
            comspec,
            "/d",
            "/c",
            "call",
            str(existing),
            "auth",
            "print-identity-token",
        ], str(existing)

    if suffix == ".ps1":
        powershell = (
            shutil.which("powershell.exe")
            or shutil.which("pwsh.exe")
            or "powershell.exe"
        )
        return [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(existing),
            "auth",
            "print-identity-token",
        ], str(existing)

    return [str(existing), "auth", "print-identity-token"], str(existing)


async def _gcloud_cli_identity_token(audience: str) -> str:
    """Acquire an identity token from the local Google Cloud CLI for Windows/local dev."""
    cache_key = f"gcloud:{audience}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and cached[1] - 120 > now:
        return cached[0]

    def _run() -> str:
        command, launcher = _resolve_gcloud_invocation()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Resolved Google Cloud CLI launcher {launcher!r}, but its host process could not be started: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Google Cloud CLI identity-token command timed out via {launcher!r}."
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "gcloud identity-token command failed").strip()
            raise RuntimeError(
                f"Google Cloud CLI identity-token command failed via {launcher!r}: {detail}"
            )
        return proc.stdout.strip()

    token = await asyncio.to_thread(_run)
    if not token:
        raise RuntimeError("gcloud returned an empty identity token.")
    exp = _jwt_exp(token) or (time.time() + 2700)
    _CACHE[cache_key] = (token, exp)
    return token


async def apply_slm_auth(
    headers: MutableMapping[str, str],
    *,
    base_url: str,
) -> MutableMapping[str, str]:
    """Apply deployment-only SLM authentication without changing local behavior.

    none/api_key/bearer: preserve the caller's existing Authorization behavior.
    gcp_identity: use the Cloud Run metadata identity token when CARDINAL is deployed.
    gcloud_cli: use `gcloud auth print-identity-token` for local Windows development.
    """
    mode = _auth_mode()
    if mode in {"", "none", "api_key", "bearer"}:
        return headers

    audience = _audience(base_url)
    if mode == "gcp_identity":
        token = await _metadata_identity_token(audience)
    elif mode == "gcloud_cli":
        token = await _gcloud_cli_identity_token(audience)
    else:
        raise RuntimeError(f"Unsupported SLM_AUTH_MODE={mode!r}.")
    headers["Authorization"] = f"Bearer {token}"
    return headers
