import logging
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nba_today.transport.base import Message, TransportError
from nba_today.transport.outbox import OutboxTransport

MESSAGE = Message(
    subject="NBA Today — Oct 19",
    html="<p>digest</p>",
    text="digest",
    to=["fan@example.com"],
    from_="nba@example.com",
)


def _today(tz: str = "America/New_York") -> str:
    return datetime.now(UTC).astimezone(ZoneInfo(tz)).date().isoformat()


def test_writes_html_and_text_named_for_the_local_date(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"

    receipt = OutboxTransport(outbox).send(MESSAGE)

    day = _today()
    assert (outbox / f"{day}.html").read_text() == "<p>digest</p>"
    assert (outbox / f"{day}.txt").read_text() == "digest"
    assert receipt.transport == "outbox"
    assert receipt.message_id == str(outbox / f"{day}.html")
    assert receipt.sent_at.tzinfo is UTC


def test_date_follows_the_configured_zone(tmp_path: Path) -> None:
    receipt = OutboxTransport(tmp_path, tz=ZoneInfo("Pacific/Kiritimati")).send(MESSAGE)

    assert receipt.message_id == str(tmp_path / f"{_today('Pacific/Kiritimati')}.html")


def test_logs_where_it_wrote(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="nba_today.transport.outbox"):
        receipt = OutboxTransport(tmp_path).send(MESSAGE)

    [record] = caplog.records
    assert vars(record)["path"] == receipt.message_id
    assert vars(record)["subject"] == MESSAGE.subject


def test_unwritable_directory_is_a_transport_error(tmp_path: Path) -> None:
    blocker = tmp_path / "outbox"
    blocker.write_text("not a directory")

    with pytest.raises(TransportError, match="outbox"):
        OutboxTransport(blocker).send(MESSAGE)


def test_a_given_day_names_the_files_instead_of_the_send_date(tmp_path: Path) -> None:
    receipt = OutboxTransport(tmp_path, day=date(2026, 6, 15)).send(MESSAGE)

    assert receipt.message_id == str(tmp_path / "2026-06-15.html")
    assert (tmp_path / "2026-06-15.txt").read_text() == "digest"
