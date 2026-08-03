from __future__ import annotations

import asyncio
import json

from app.api_range_waveforms import (
    fetch_api_payload,
)


async def main() -> int:
    payload = await fetch_api_payload()

    counts = {
        key: len(value)
        for key, value in payload.items()
        if isinstance(value, list)
    }

    print(
        json.dumps(
            {
                "status": "PASS",
                "topLevelKeys": sorted(
                    payload.keys()
                ),
                "arrayCounts": counts,
            },
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(main())
    )
