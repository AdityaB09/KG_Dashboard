from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from typing import Any

from .repository import (
    list_episode_ids,
)
from .service import (
    run_all,
    run_episode,
)


def _format_seconds(
    value: float | None,
) -> str:
    if value is None:
        return "--:--"

    seconds = max(
        0,
        int(round(value)),
    )
    minutes, seconds = divmod(
        seconds,
        60,
    )
    hours, minutes = divmod(
        minutes,
        60,
    )

    if hours:
        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def _progress_bar(
    completed: int,
    total: int,
    width: int,
) -> str:
    ratio = (
        completed / total
        if total
        else 0.0
    )

    filled = min(
        width,
        max(
            0,
            int(round(ratio * width)),
        ),
    )

    return (
        "["
        + "#" * filled
        + "-" * (width - filled)
        + "]"
    )


class CompactConsoleProgress:
    """
    One compact in-place terminal line.

    It prints at most once every progress_interval seconds,
    unless the episode changes or completes.
    """

    def __init__(
        self,
        progress_interval: float = 5.0,
    ) -> None:
        self.total = 0
        self.last_printed_at = 0.0
        self.last_line_length = 0
        self.progress_interval = max(
            1.0,
            progress_interval,
        )

    def _clear_current_line(self) -> None:
        if self.last_line_length <= 0:
            return

        print(
            "\r"
            + (" " * self.last_line_length)
            + "\r",
            end="",
            flush=True,
        )

        self.last_line_length = 0

    def _write_in_place(
        self,
        line: str,
    ) -> None:
        terminal_width = (
            shutil.get_terminal_size(
                fallback=(100, 24)
            ).columns
        )

        safe_width = max(
            40,
            terminal_width - 2,
        )

        if len(line) > safe_width:
            line = (
                line[: safe_width - 3]
                + "..."
            )

        padding = max(
            0,
            self.last_line_length
            - len(line),
        )

        print(
            "\r"
            + line
            + (" " * padding),
            end="",
            flush=True,
        )

        self.last_line_length = len(line)

    def __call__(
        self,
        event: dict[str, Any],
    ) -> None:
        event_type = event.get("type")

        if event_type == "batch_start":
            self.total = int(
                event.get("total", 0)
            )

            print()
            print("CARDINAL SLM evaluation")
            print(
                f"Model: {event.get('model')}"
            )
            print(
                "Cases: "
                f"{event.get('total')} "
                "| Pending: "
                f"{event.get('pending')} "
                "| Already complete: "
                f"{event.get('skipped')}"
            )
            print(
                "Maximum per case: "
                f"{_format_seconds(event.get('episodeTimeoutSeconds'))}"
            )
            print()
            return

        if event_type == "episode_skipped":
            print(
                "✓ Skipped completed: "
                f"{event.get('episodeId')}"
            )
            return

        if event_type == "episode_start":
            self._clear_current_line()
            self.last_printed_at = 0.0

            current = int(
                event.get("completed", 0)
            ) + 1
            total = int(
                event.get("total", self.total)
            )

            print(
                f"\nRunning {current}/{total}: "
                f"{event.get('episodeId')}"
            )
            return

        if event_type == "episode_tick":
            now = time.monotonic()

            if (
                now - self.last_printed_at
                < self.progress_interval
            ):
                return

            self.last_printed_at = now

            completed = int(
                event.get("completed", 0)
            )
            total = int(
                event.get("total", self.total)
            )

            terminal_width = (
                shutil.get_terminal_size(
                    fallback=(100, 24)
                ).columns
            )

            bar_width = 12
            if terminal_width >= 120:
                bar_width = 18
            elif terminal_width < 80:
                bar_width = 8

            line = (
                f"{_progress_bar(completed, total, bar_width)} "
                f"{completed}/{total} "
                f"| elapsed "
                f"{_format_seconds(event.get('elapsedSeconds'))} "
                f"| limit left "
                f"{_format_seconds(event.get('currentTimeoutRemainingSeconds'))} "
                f"| total ETA "
                f"{_format_seconds(event.get('estimatedTotalRemainingSeconds'))}"
            )

            self._write_in_place(line)
            return

        if event_type == "episode_complete":
            self._clear_current_line()

            score = (
                event.get("score", {})
                or {}
            )

            print(
                "✓ Completed "
                f"{event.get('episodeId')} "
                "| time "
                f"{_format_seconds(event.get('durationSeconds'))} "
                "| score "
                f"{score.get('total')} "
                "| safety "
                f"{'PASS' if score.get('safetyPass') else 'FAIL'}"
            )
            return

        if event_type == "episode_failed":
            self._clear_current_line()

            print(
                "✗ "
                f"{event.get('episodeId')} "
                f"{event.get('status')} "
                "| "
                f"{event.get('message')}"
            )
            return

        if event_type == "batch_complete":
            self._clear_current_line()

            summary = event.get(
                "summary",
                {},
            )

            print()
            print("Evaluation finished")
            print(
                "Completed: "
                f"{summary.get('completedCount')} "
                "| Skipped: "
                f"{summary.get('skippedCount')} "
                "| Timed out: "
                f"{summary.get('timeoutCount')} "
                "| Failed: "
                f"{summary.get('failedCount')}"
            )
            print(
                "Average score: "
                f"{summary.get('averageScore')} "
                "| Average time: "
                f"{_format_seconds(summary.get('averageDurationSeconds'))}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the isolated CARDINAL "
            "SLM evaluator."
        )
    )

    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--episode",
        help="Run one episode ID.",
    )

    group.add_argument(
        "--all",
        action="store_true",
        help="Run all eight cases.",
    )

    group.add_argument(
        "--list",
        action="store_true",
        help="List episode IDs.",
    )

    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Optional model override. "
            "Otherwise SLM_MODEL is used."
        ),
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
    )

    parser.add_argument(
        "--episode-timeout",
        type=float,
        default=420.0,
        help=(
            "Hard wall-clock seconds "
            "allowed per case. Default: 420."
        ),
    )

    parser.add_argument(
        "--estimated-seconds-per-episode",
        type=float,
        default=300.0,
        help=(
            "Initial ETA per remaining case. "
            "Default: 300."
        ),
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip cases already completed "
            "for the same model."
        ),
    )

    parser.add_argument(
        "--progress-interval",
        type=float,
        default=5.0,
        help=(
            "Seconds between progress refreshes. "
            "Default: 5."
        ),
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the live progress line.",
    )

    return parser


async def async_main() -> int:
    args = (
        build_parser()
        .parse_args()
    )

    if args.list:
        print(
            json.dumps(
                {
                    "episodes": (
                        list_episode_ids()
                    )
                },
                indent=2,
            )
        )
        return 0

    if args.all:
        callback = (
            None
            if args.no_progress
            else CompactConsoleProgress(
                progress_interval=(
                    args.progress_interval
                )
            )
        )

        result = await run_all(
            model_override=args.model,
            temperature=args.temperature,
            episode_timeout_seconds=(
                args.episode_timeout
            ),
            initial_estimate_seconds=(
                args.estimated_seconds_per_episode
            ),
            resume=args.resume,
            progress_callback=callback,
        )
    else:
        print(
            f"Running: {args.episode}"
        )

        result = await asyncio.wait_for(
            run_episode(
                episode_id=args.episode,
                model_override=args.model,
                temperature=args.temperature,
            ),
            timeout=args.episode_timeout,
        )

    print()
    print("FINAL_JSON:")
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    return 0


def main() -> None:
    try:
        code = asyncio.run(
            async_main()
        )
    except KeyboardInterrupt:
        print(
            "\nEvaluation interrupted. "
            "Completed runs remain saved. "
            "Continue with --all --resume.",
            file=sys.stderr,
        )
        raise SystemExit(130)
    except asyncio.TimeoutError:
        print(
            "\nThe evaluation case reached "
            "its hard timeout.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    except Exception as exc:
        print(
            "\nEvaluation failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    raise SystemExit(code)


if __name__ == "__main__":
    main()
