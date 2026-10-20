"""The last-sent marker on the data volume: what stops a restart from sending twice (spec §3.3)."""

import datetime as dt
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from nba_today.atomic import write_atomic
from nba_today.digest.models import DigestKind

log = logging.getLogger(__name__)


class Marker(BaseModel):
    """The last digest sent, and when the scheduler last planned; every field `None` until set."""

    model_config = ConfigDict(frozen=True)

    # `dt.` because a field named `date` shadows the type for the annotations after it.
    date: dt.date | None = None
    kind: DigestKind | None = None
    sent_at: dt.datetime | None = None
    transport: str | None = None
    message_id: str | None = None
    planned_at: dt.datetime | None = None


class State:
    """The marker in one JSON file, `DATA_DIR/state.json`; written whole and atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Marker | None:
        """The stored marker, or `None` if there is none or it cannot be read."""
        try:
            return Marker.model_validate_json(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValidationError):
            log.error("unreadable state file", extra={"path": str(self.path)}, exc_info=True)
            return None

    def write(self, marker: Marker) -> None:
        write_atomic(self.path, marker.model_dump_json())

    def touch_planned(self, now: dt.datetime) -> None:
        """Record a plan at `now`, keeping whatever the marker already says about the last send."""
        self.write((self.read() or Marker()).model_copy(update={"planned_at": now}))
