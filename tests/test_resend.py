from collections.abc import Mapping
from datetime import UTC
from json import dumps
from typing import Any

import pytest
import resend
from resend.exceptions import ResendError

from nba_today.transport.base import Message, TransportError
from nba_today.transport.resend import ResendTransport

MESSAGE = Message(
    subject="NBA Today — Oct 19",
    html="<p>digest</p>",
    text="digest",
    to=["a@example.com", "b@example.com"],
    from_="NBA Today <nba@example.com>",
)


class FakeHTTPClient(resend.HTTPClient):
    """Stands in for the SDK's HTTP client: answers every request with one canned response."""

    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json: dict[str, object] | list[object] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[bytes, int, Mapping[str, str]]:
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        if self.body is None:
            raise RuntimeError("Request failed: connection refused")
        content = dumps(self.body).encode()
        return content, self.status, {"content-type": "application/json"}


def _install(monkeypatch: pytest.MonkeyPatch, client: FakeHTTPClient) -> FakeHTTPClient:
    monkeypatch.setattr(resend, "default_http_client", client)
    monkeypatch.setattr(resend, "api_key", None)
    return client


def _transport() -> ResendTransport:
    return ResendTransport("re_test_key", "NBA Today <nba@tomboone.com>")


def test_sends_from_the_configured_sender_and_returns_the_resend_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch, FakeHTTPClient(200, {"id": "49a3999c-0ce1"}))

    receipt = _transport().send(MESSAGE)

    [req] = client.requests
    assert (req["method"], req["url"]) == ("post", "https://api.resend.com/emails")
    assert req["headers"]["Authorization"] == "Bearer re_test_key"
    assert req["json"] == {
        "from": "NBA Today <nba@tomboone.com>",
        "to": ["a@example.com", "b@example.com"],
        "subject": "NBA Today — Oct 19",
        "html": "<p>digest</p>",
        "text": "digest",
    }
    assert receipt.transport == "resend"
    assert receipt.message_id == "49a3999c-0ce1"
    assert receipt.sent_at.tzinfo is UTC


def test_an_accepted_send_without_an_id_is_still_a_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeHTTPClient(200, {}))

    receipt = _transport().send(MESSAGE)

    assert receipt.transport == "resend"
    assert receipt.message_id is None


def test_an_api_error_is_a_transport_error_carrying_the_resend_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "statusCode": 422,
        "name": "validation_error",
        "message": "The tomboone.com domain is not verified.",
    }
    _install(monkeypatch, FakeHTTPClient(422, body))

    with pytest.raises(TransportError, match=r"tomboone\.com domain is not verified") as raised:
        _transport().send(MESSAGE)

    assert isinstance(raised.value.__cause__, ResendError)


def test_a_network_failure_is_a_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeHTTPClient(0))

    with pytest.raises(TransportError, match="connection refused"):
        _transport().send(MESSAGE)
