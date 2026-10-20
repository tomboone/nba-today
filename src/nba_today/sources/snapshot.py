"""Source snapshots: the last good raw response per source, kept on the data volume (ADR-0002)."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nba_today.atomic import write_atomic

type Payload = dict[str, Any]

log = logging.getLogger(__name__)


class SnapshotStore:
    """Files `<root>/snapshots/<source>.json`, each `{"fetched_at": <iso>, "payload": {...}}`."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "snapshots"

    def _path(self, source: str) -> Path:
        return self._dir / f"{source}.json"

    def get(self, source: str) -> tuple[Payload, datetime] | None:
        """The stored payload and when it was fetched, or None if there is no usable snapshot."""
        path = self._path(source)
        try:
            stored = json.loads(path.read_text())
            return stored["payload"], datetime.fromisoformat(stored["fetched_at"])
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError):
            log.warning("unreadable snapshot", extra={"source": source, "path": str(path)})
            return None

    def put(self, source: str, payload: Payload) -> datetime:
        """Store `payload` as the latest snapshot, atomically; return the `fetched_at` recorded."""
        fetched_at = datetime.now(UTC)
        body = json.dumps({"fetched_at": fetched_at.isoformat(), "payload": payload})
        write_atomic(self._path(source), body)
        return fetched_at
