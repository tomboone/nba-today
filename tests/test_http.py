from collections.abc import Iterator

import httpx
import pytest
import respx

from nba_today.sources import http
from nba_today.sources.http import make_client

BASE = "https://api.example.com"


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(http.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with make_client(BASE, "nba-today-test/1.0") as c:
        yield c


@respx.mock
def test_two_503s_then_200_succeeds_after_three_requests(
    client: httpx.Client, sleeps: list[float]
) -> None:
    route = respx.get(f"{BASE}/schedule").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={})]
    )

    response = client.get("/schedule")

    assert response.status_code == 200
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_gives_up_after_three_retries_and_returns_last_5xx(
    client: httpx.Client, sleeps: list[float]
) -> None:
    route = respx.get(f"{BASE}/schedule").mock(return_value=httpx.Response(502))

    response = client.get("/schedule")

    assert response.status_code == 502
    assert route.call_count == 4
    assert sleeps == [0.5, 1.0, 2.0]


@respx.mock
def test_retries_connect_errors_then_reraises(client: httpx.Client) -> None:
    route = respx.get(f"{BASE}/schedule").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        client.get("/schedule")

    assert route.call_count == 4


@respx.mock
def test_connect_error_then_success(client: httpx.Client) -> None:
    route = respx.get(f"{BASE}/schedule").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json={})]
    )

    assert client.get("/schedule").status_code == 200
    assert route.call_count == 2


@respx.mock
def test_4xx_is_not_retried(client: httpx.Client, sleeps: list[float]) -> None:
    route = respx.get(f"{BASE}/schedule").mock(return_value=httpx.Response(404))

    assert client.get("/schedule").status_code == 404
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_read_timeout_is_not_retried(client: httpx.Client) -> None:
    route = respx.get(f"{BASE}/schedule").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(httpx.ReadTimeout):
        client.get("/schedule")

    assert route.call_count == 1


@respx.mock
def test_sends_user_agent_and_sets_timeout(client: httpx.Client) -> None:
    route = respx.get(f"{BASE}/schedule").mock(return_value=httpx.Response(200))

    client.get("/schedule")

    assert route.calls.last.request.headers["User-Agent"] == "nba-today-test/1.0"
    assert client.timeout == httpx.Timeout(20.0)


def test_default_user_agent_names_the_app() -> None:
    with make_client(BASE) as c:
        assert c.headers["User-Agent"].startswith("nba-today/")
