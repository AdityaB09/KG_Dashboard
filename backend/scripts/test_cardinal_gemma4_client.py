from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env", override=True)

from app.evaluation.slm_client import call_model  # noqa: E402


async def main() -> int:
    print("CARDINAL provider:", os.getenv("CARDINAL_LLM_PROVIDER"))
    print("CARDINAL model:", os.getenv("SLM_MODEL"))
    print("CARDINAL endpoint:", f"{os.getenv('SLM_BASE_URL')}{os.getenv('SLM_CHAT_PATH')}")

    result, metadata = await call_model(
        messages=[
            {
                "role": "user",
                "content": (
                    'Return ONLY valid JSON with exactly this object: '
                    '{"status":"CARDINAL_BACKEND_GEMMA_OK"}'
                ),
            }
        ],
        temperature=0.0,
    )

    print("RESULT:", result)
    print("METADATA:", metadata)
    if result.get("status") != "CARDINAL_BACKEND_GEMMA_OK":
        raise SystemExit("Gemma responded, but CARDINAL JSON result did not match expected test value.")
    if metadata.get("name") != "gemma4-31b":
        raise SystemExit("CARDINAL did not use gemma4-31b.")
    if metadata.get("provider") != "gemma4":
        raise SystemExit("CARDINAL provider metadata is not gemma4.")
    print("CARDINAL_BACKEND_GEMMA_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
