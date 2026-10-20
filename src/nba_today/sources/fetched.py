"""Fetch a source, or fall back to its snapshot and say so (spec §3.2, ADR-0002)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from nba_today.sources.snapshot import Payload, SnapshotStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Fetched[T]:
    data: T
    fetched_at: datetime
    stale: bool


class SourceUnavailable(Exception):
    """The source failed and there is no usable snapshot to fall back to."""

    def __init__(self, source: str) -> None:
        super().__init__(f"{source} unavailable and no snapshot to fall back to")
        self.source = source


def fetch_or_snapshot[T](
    source: str,
    fetch_fn: Callable[[], Payload] | None,
    parse_fn: Callable[[Payload], T],
    store: SnapshotStore,
) -> Fetched[T]:
    """Fetch, parse, snapshot, and return fresh data; on any failure, parse the snapshot instead.

    `fetch_fn` must raise on failure: the HTTP client returns a 5xx that outlived its retries as
    an ordinary response, so call `raise_for_status()` before returning the JSON. A `None`
    `fetch_fn` skips the network and reads the snapshot alone (`preview --from-snapshots`).

    The fresh payload is parsed before it is stored, so a response that does not parse never
    replaces a good snapshot. A snapshot that cannot be written is logged, not raised: the fresh
    data is still good (ADR-0002).
    """
    if fetch_fn is None:
        return _from_snapshot(source, parse_fn, store)
    try:
        payload = fetch_fn()
    except Exception:
        log.warning("source fetch failed", extra={"source": source}, exc_info=True)
    else:
        try:
            data = parse_fn(payload)
        except Exception:
            log.error("source payload did not parse", extra={"source": source}, exc_info=True)
        else:
            try:
                fetched_at = store.put(source, payload)
            except OSError:
                log.error("snapshot write failed", extra={"source": source}, exc_info=True)
                fetched_at = datetime.now(UTC)
            return Fetched(data, fetched_at, stale=False)

    fetched = _from_snapshot(source, parse_fn, store)
    log.warning(
        "using snapshot", extra={"source": source, "fetched_at": fetched.fetched_at.isoformat()}
    )
    return fetched


def _from_snapshot[T](
    source: str, parse_fn: Callable[[Payload], T], store: SnapshotStore
) -> Fetched[T]:
    snapshot = store.get(source)
    if snapshot is None:
        raise SourceUnavailable(source)
    payload, fetched_at = snapshot
    try:
        data = parse_fn(payload)
    except Exception as exc:
        raise SourceUnavailable(source) from exc
    return Fetched(data, fetched_at, stale=True)
