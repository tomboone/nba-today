import logging
from datetime import UTC, datetime

import pytest

from nba_today.transport.base import AllTransportsFailed, Message, SendReceipt, TransportError
from nba_today.transport.chain import ChainTransport

MESSAGE = Message(
    subject="NBA Today — Oct 19",
    html="<p>digest</p>",
    text="digest",
    to=["fan@example.com"],
    from_="nba@example.com",
)


class Failing:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def send(self, msg: Message) -> SendReceipt:
        self.calls += 1
        raise TransportError(f"{self.name} is down")


class Succeeding:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def send(self, msg: Message) -> SendReceipt:
        self.calls += 1
        return SendReceipt(
            transport=self.name, message_id=f"<{self.name}>", sent_at=datetime.now(UTC)
        )


def test_first_success_wins_without_trying_the_rest() -> None:
    first, second = Succeeding("a"), Succeeding("b")

    receipt = ChainTransport([first, second]).send(MESSAGE)

    assert receipt.transport == "a"
    assert (first.calls, second.calls) == (1, 0)


def test_falls_back_to_the_next_transport_and_logs_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing, succeeding = Failing("resend"), Succeeding("smtp")

    with caplog.at_level(logging.WARNING, logger="nba_today.transport.chain"):
        receipt = ChainTransport([failing, succeeding]).send(MESSAGE)

    assert receipt.transport == "smtp"
    assert receipt.message_id == "<smtp>"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert vars(warnings[0])["transport"] == "resend"


def test_all_failing_raises_with_every_error_in_order() -> None:
    with pytest.raises(AllTransportsFailed) as raised:
        ChainTransport([Failing("resend"), Failing("smtp")]).send(MESSAGE)

    errors = raised.value.errors
    assert [name for name, _ in errors] == ["resend", "smtp"]
    assert [str(exc) for _, exc in errors] == ["resend is down", "smtp is down"]


def test_non_transport_errors_propagate() -> None:
    class Broken:
        name = "broken"

        def send(self, msg: Message) -> SendReceipt:
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        ChainTransport([Broken(), Succeeding("smtp")]).send(MESSAGE)


def test_empty_chain_rejected() -> None:
    with pytest.raises(ValueError, match="at least one transport"):
        ChainTransport([])
