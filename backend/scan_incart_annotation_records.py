from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import wfdb

from app.config import settings
from app.episodes import (
    ANNOTATION_POLICIES,
    UNKNOWN_POLICY,
    normalize_annotation_symbol,
)


OUTPUT_PATH = Path(
    "data/incart_annotation_catalog.json"
)

RECORDS = [
    f"I{index:02d}"
    for index in range(1, 76)
]


def policy_for(symbol: str) -> dict[str, Any]:
    return dict(
        ANNOTATION_POLICIES.get(
            symbol,
            UNKNOWN_POLICY,
        )
    )


def scan_record(
    record_name: str,
) -> dict[str, Any]:
    header = wfdb.rdheader(
        record_name,
        pn_dir=settings.INCART_PN_DIR,
    )

    annotation = wfdb.rdann(
        record_name,
        settings.INCART_ANNOTATOR,
        pn_dir=settings.INCART_PN_DIR,
    )

    sample_rate = float(header.fs)

    symbols = [
        normalize_annotation_symbol(value)
        for value in annotation.symbol
    ]

    counts = Counter(symbols)

    first_samples: dict[str, int] = {}

    for sample, symbol in zip(
        annotation.sample,
        symbols,
    ):
        if not symbol:
            continue

        if symbol not in first_samples:
            first_samples[symbol] = int(sample)

    trigger_symbols = {}
    non_v_trigger_symbols = {}

    for symbol, count in sorted(
        counts.items()
    ):
        policy = policy_for(symbol)

        if policy.get("mode") == "context":
            continue

        first_seconds = round(
            first_samples[symbol]
            / sample_rate,
            3,
        )

        trigger_information = {
            "count": count,
            "firstSeconds": first_seconds,
            "suggestedStreamStartSeconds": round(
                max(
                    0,
                    first_seconds
                    - float(
                        settings.EPISODE_PRE_SECONDS
                    )
                    - 2,
                ),
                3,
            ),
            "category": policy.get(
                "category"
            ),
            "display": policy.get(
                "display"
            ),
            "severity": policy.get(
                "severity"
            ),
            "mode": policy.get(
                "mode"
            ),
        }

        trigger_symbols[symbol] = (
            trigger_information
        )

        if symbol != "V":
            non_v_trigger_symbols[symbol] = (
                trigger_information
            )

    first_trigger_seconds = min(
        (
            value["firstSeconds"]
            for value
            in trigger_symbols.values()
        ),
        default=None,
    )

    first_non_v_trigger_seconds = min(
        (
            value["firstSeconds"]
            for value
            in non_v_trigger_symbols.values()
        ),
        default=None,
    )

    return {
        "record": record_name,
        "durationSeconds": round(
            float(header.sig_len)
            / sample_rate,
            3,
        ),
        "sampleRate": sample_rate,
        "totalAnnotations": len(symbols),
        "annotationCounts": dict(
            sorted(counts.items())
        ),
        "triggerSymbols": trigger_symbols,
        "nonVTriggerSymbols": (
            non_v_trigger_symbols
        ),
        "triggerSymbolCount": len(
            trigger_symbols
        ),
        "nonVTriggerSymbolCount": len(
            non_v_trigger_symbols
        ),
        "firstTriggerSeconds": (
            first_trigger_seconds
        ),
        "firstNonVTriggerSeconds": (
            first_non_v_trigger_seconds
        ),
    }


def main() -> None:
    results = []
    failures = []

    for record_name in RECORDS:
        try:
            result = scan_record(
                record_name
            )

            results.append(result)

            non_v = list(
                result[
                    "nonVTriggerSymbols"
                ].keys()
            )

            print(
                f"{record_name}: "
                f"{result['annotationCounts']}"
            )

            if non_v:
                print(
                    "  non-V triggers:",
                    ", ".join(non_v),
                    "first:",
                    result[
                        "firstNonVTriggerSeconds"
                    ],
                    "seconds",
                )

        except Exception as error:
            failures.append(
                {
                    "record": record_name,
                    "error": str(error),
                }
            )

            print(
                f"{record_name}: ERROR "
                f"{error}"
            )

    recommended_records = sorted(
        [
            {
                "record": item["record"],
                "symbols": list(
                    item[
                        "nonVTriggerSymbols"
                    ].keys()
                ),
                "firstNonVTriggerSeconds": (
                    item[
                        "firstNonVTriggerSeconds"
                    ]
                ),
                "annotationCounts": (
                    item[
                        "annotationCounts"
                    ]
                ),
            }
            for item in results
            if item[
                "nonVTriggerSymbolCount"
            ] > 0
        ],
        key=lambda item: (
            item[
                "firstNonVTriggerSeconds"
            ]
            if item[
                "firstNonVTriggerSeconds"
            ]
            is not None
            else float("inf")
        ),
    )

    output = {
        "database": "incartdb",
        "recordCount": len(results),
        "failedRecordCount": len(
            failures
        ),
        "recordsWithNonVTriggers": len(
            recommended_records
        ),
        "recommendedRecords": (
            recommended_records
        ),
        "records": results,
        "failures": failures,
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "Saved:",
        OUTPUT_PATH.resolve(),
    )

    print()
    print(
        "Recommended records with "
        "non-V triggers:"
    )

    for item in recommended_records[
        :20
    ]:
        print(
            f"{item['record']}: "
            f"symbols={item['symbols']} "
            f"first="
            f"{item['firstNonVTriggerSeconds']}s"
        )


if __name__ == "__main__":
    main()