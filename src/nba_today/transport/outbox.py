"""The dry-run transport: write the digest to the data volume instead of sending it (spec §7)."""

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nba_today.transport.base import Message, SendReceipt, TransportError

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")


class OutboxTransport:
    """Files `<directory>/<date>.html` and `<date>.txt`.

    `<date>` is `day` when given (the digest's date, so `send --date` backfills land under the
    date they show), else the local send date. A second send on the same date replaces the first.
    """

    name = "outbox"

    def __init__(self, directory: Path, *, tz: ZoneInfo = EASTERN, day: date | None = None) -> None:
        self.directory = directory
        self._tz = tz
        self._day = day

    def send(self, msg: Message) -> SendReceipt:
        sent_at = datetime.now(UTC)
        day = (self._day or sent_at.astimezone(self._tz).date()).isoformat()
        html_path = self.directory / f"{day}.html"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            html_path.write_text(msg.html)
            (self.directory / f"{day}.txt").write_text(msg.text)
        except OSError as exc:
            raise TransportError(f"outbox write to {self.directory} failed: {exc}") from exc
        log.info(
            "dry run: digest written to outbox",
            extra={"path": str(html_path), "subject": msg.subject, "to": msg.to},
        )
        return SendReceipt(transport=self.name, message_id=str(html_path), sent_at=sent_at)
