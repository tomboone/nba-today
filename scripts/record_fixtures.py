"""Record trimmed live ESPN and NBA.com responses into tests/fixtures/ (spec §10).

    uv run scripts/record_fixtures.py --date 2026-03-14 [RECORDER ...]

Each recorder is registered by name with `@recorder` and writes into `tests/fixtures/espn/` or
`tests/fixtures/nba/` through `write_fixture`. The source tickets add recorders as they add
clients; with no names given, every registered recorder runs.
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

type Payload = dict[str, Any]
type Source = Literal["espn", "nba"]
type RecordFn = Callable[[date], None]


@dataclass(frozen=True, slots=True)
class Recorder:
    record: RecordFn
    help: str


RECORDERS: dict[str, Recorder] = {}


def recorder(name: str, help: str) -> Callable[[RecordFn], RecordFn]:
    """Register `fn` under `name`; `fn` receives the `--date` and writes its own fixtures."""

    def register(fn: RecordFn) -> RecordFn:
        RECORDERS[name] = Recorder(fn, help)
        return fn

    return register


def write_fixture(source: Source, name: str, payload: Payload) -> None:
    path = FIXTURES / source / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(FIXTURES.parent.parent)}")


def _registry() -> str:
    if not RECORDERS:
        return "recorders:\n  (none registered yet)"
    width = max(map(len, RECORDERS))
    lines = [f"  {name:<{width}}  {r.help}" for name, r in sorted(RECORDERS.items())]
    return "recorders:\n" + "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record live fixtures.",
        epilog=_registry(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "recorders", nargs="*", metavar="RECORDER", help="recorders to run (default: all)"
    )
    args = parser.parse_args()
    day: date = args.date
    names: list[str] = args.recorders or sorted(RECORDERS)

    unknown = [n for n in names if n not in RECORDERS]
    if unknown:
        parser.error(f"unknown recorder(s): {', '.join(unknown)}")
    if not names:
        parser.error("no recorders registered yet")
    for name in names:
        RECORDERS[name].record(day)


if __name__ == "__main__":
    main()
