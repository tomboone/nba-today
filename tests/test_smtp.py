import smtplib
import ssl
from datetime import UTC
from email.message import EmailMessage
from typing import Any, ClassVar, Self

import pytest

from nba_today.transport import smtp
from nba_today.transport.base import Message, TransportError
from nba_today.transport.smtp import SmtpTransport

MESSAGE = Message(
    subject="NBA Today — Oct 19",
    html="<p>digest</p>",
    text="digest",
    to=["a@example.com", "b@example.com"],
    from_="NBA Today <nba@example.com>",
)


class FakeSMTP:
    """Stands in for `smtplib.SMTP_SSL`, recording what the transport did with it."""

    instances: ClassVar[list["FakeSMTP"]] = []
    fail_with: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, *, context: ssl.SSLContext, timeout: float) -> None:
        self.host, self.port, self.context, self.timeout = host, port, context, timeout
        self.logins: list[tuple[str, str]] = []
        self.sent: list[tuple[EmailMessage, str, list[str]]] = []
        self.closed = False
        FakeSMTP.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        self.logins.append((user, password))

    def send_message(
        self, msg: EmailMessage, from_addr: str, to_addrs: list[str]
    ) -> dict[str, Any]:
        self.sent.append((msg, from_addr, to_addrs))
        return {}


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", FakeSMTP)


def _transport() -> SmtpTransport:
    return SmtpTransport("smtp.example.com", 465, "user", "secret", "nba@example.com")


def test_sends_multipart_alternative_over_implicit_tls() -> None:
    receipt = _transport().send(MESSAGE)

    [conn] = FakeSMTP.instances
    assert (conn.host, conn.port) == ("smtp.example.com", 465)
    assert isinstance(conn.context, ssl.SSLContext)
    assert conn.logins == [("user", "secret")]
    assert conn.closed
    [(email, from_addr, to_addrs)] = conn.sent
    assert from_addr == "nba@example.com"
    assert to_addrs == ["a@example.com", "b@example.com"]
    assert email["Subject"] == "NBA Today — Oct 19"
    assert email["From"] == "nba@example.com"
    assert email["To"] == "a@example.com, b@example.com"
    assert email.get_content_type() == "multipart/alternative"
    parts = list(email.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert [p.get_content().strip() for p in parts if isinstance(p, EmailMessage)] == [
        "digest",
        "<p>digest</p>",
    ]
    assert receipt.transport == "smtp"
    assert receipt.message_id == email["Message-ID"]
    assert receipt.message_id is not None and receipt.message_id.endswith("@example.com>")
    assert receipt.sent_at.tzinfo is UTC


@pytest.mark.parametrize(
    "error",
    [smtplib.SMTPAuthenticationError(535, b"bad credentials"), ConnectionRefusedError("refused")],
)
def test_smtp_and_socket_failures_are_transport_errors(error: Exception) -> None:
    FakeSMTP.fail_with = error

    with pytest.raises(TransportError, match=r"smtp\.example\.com") as raised:
        _transport().send(MESSAGE)

    assert raised.value.__cause__ is error
