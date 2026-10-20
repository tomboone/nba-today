import logging
from collections.abc import Iterator

import httpx
import pytest
import respx

from nba_today.heartbeat import TIMEOUT_SECONDS, Heartbeat

URL = "https://hc-ping.com/abc-123"


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


@respx.mock
def test_ok_gets_the_url(client: httpx.Client) -> None:
    route = respx.get(URL).respond(200, text="OK")

    Heartbeat(URL, client).ok()

    assert route.call_count == 1


@respx.mock
def test_fail_posts_the_reason_to_the_fail_url(client: httpx.Client) -> None:
    route = respx.post(f"{URL}/fail").respond(200, text="OK")

    Heartbeat(URL, client).fail("all transports failed")

    assert route.call_count == 1
    assert route.calls.last.request.content == b"all transports failed"


@respx.mock
def test_trailing_slash_on_the_url_is_ignored(client: httpx.Client) -> None:
    route = respx.post(f"{URL}/fail").respond(200, text="OK")

    Heartbeat(f"{URL}/", client).fail("all transports failed")

    assert route.call_count == 1


@respx.mock
def test_pings_use_the_heartbeat_timeout(client: httpx.Client) -> None:
    route = respx.get(URL).respond(200)

    Heartbeat(URL, client).ok()

    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == httpx.Timeout(TIMEOUT_SECONDS).as_dict()


@pytest.mark.respx(assert_all_called=False)
def test_unset_url_sends_nothing(
    client: httpx.Client, caplog: pytest.LogCaptureFixture, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.route()

    with caplog.at_level(logging.DEBUG, logger="nba_today.heartbeat"):
        heartbeat = Heartbeat(None, client)
        heartbeat.ok()
        heartbeat.fail("all transports failed")

    assert route.call_count == 0
    assert caplog.messages == ["heartbeat disabled"] * 2


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404, text="not found"), httpx.ConnectError("refused")],
    ids=["error-status", "connect-error"],
)
@respx.mock
def test_ping_failures_are_logged_not_raised(
    client: httpx.Client,
    caplog: pytest.LogCaptureFixture,
    response: httpx.Response | Exception,
) -> None:
    respx.get(URL).mock(side_effect=[response])

    with caplog.at_level(logging.WARNING, logger="nba_today.heartbeat"):
        Heartbeat(URL, client).ok()

    assert caplog.messages == ["heartbeat ping failed"]
    # Anyone holding the URL can ping the check, so it never reaches the logs.
    assert not any(URL in str(record.__dict__) for record in caplog.records)


def test_malformed_url_is_logged_not_raised(
    client: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="nba_today.heartbeat"):
        Heartbeat("https://hc-ping.com/abc\x00", client).fail("all transports failed")

    assert caplog.messages == ["heartbeat ping failed"]
