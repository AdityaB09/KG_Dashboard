from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response

app = FastAPI(title="KardioGenics Local SLM Gateway")

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "hf.co/mradermacher/Med-Qwen2-7B-GGUF:Q4_K_M",
).strip()

GATEWAY_API_KEY = os.getenv(
    "GATEWAY_API_KEY",
    "",
).strip()

if not GATEWAY_API_KEY:
    raise RuntimeError(
        "GATEWAY_API_KEY must be configured."
    )


def verify_authorization(
    authorization: str | None,
) -> None:
    expected = f"Bearer {GATEWAY_API_KEY}"

    if authorization != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid gateway API key.",
        )


@app.get("/health")
async def health() -> dict[str, object]:
    try:
        async with httpx.AsyncClient(
            timeout=10,
        ) as client:
            response = await client.get(
                f"{OLLAMA_BASE_URL}/api/tags"
            )

        return {
            "ok": response.is_success,
            "ollamaReachable": response.is_success,
            "model": OLLAMA_MODEL,
        }
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "ollamaReachable": False,
            "model": OLLAMA_MODEL,
            "error": str(error),
        }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(
        default=None
    ),
) -> Response:
    verify_authorization(authorization)

    payload = await request.json()

    # The public caller is not allowed to select
    # an arbitrary locally installed model.
    payload["model"] = OLLAMA_MODEL
    payload["stream"] = False

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300),
        ) as client:
            response = await client.post(
                (
                    f"{OLLAMA_BASE_URL}"
                    "/v1/chat/completions"
                ),
                json=payload,
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama request failed: {error}",
        ) from error

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get(
            "content-type",
            "application/json",
        ),
    )