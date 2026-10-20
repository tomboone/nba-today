from collections.abc import Iterator

import pytest
from curl_cffi import requests

from nba_today.sources import nba_http
from nba_today.sources.nba import Session
from nba_today.sources.nba_http import NbaSession, make_nba_session

URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(nba_http.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def session() -> Iterator[NbaSession]:
    with make_nba_session() as s:
        yield s


def _response(status: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    return response


class ScriptedGet:
    """Stands in for `curl_cffi.Session.get`: returns or raises each scripted outcome in turn."""

    def __init__(self, *outcomes: requests.Response | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def __call__(self, url: str, params: dict[str, str] | None = None) -> requests.Response:
        self.calls.append((url, params))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_session_impersonates_chrome_with_nba_com_headers(session: NbaSession) -> None:
    assert session.curl.impersonate == "chrome"
    assert session.curl.headers["Origin"] == "https://www.nba.com"
    assert session.curl.headers["Referer"] == "https://www.nba.com/"
    assert session.curl.headers["Accept"] == "application/json"
    assert session.curl.timeout == 20.0


def test_session_satisfies_the_client_protocol(session: NbaSession) -> None:
    client: Session = session

    assert client is session


def test_two_503s_then_200_succeeds_after_three_requests(
    session: NbaSession, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    get = ScriptedGet(_response(503), _response(503), _response(200))
    monkeypatch.setattr(session.curl, "get", get)

    response = session.get(URL, params={"a": "1"})

    assert response.status_code == 200
    assert get.calls == [(URL, {"a": "1"})] * 3
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_three_retries_and_returns_last_5xx(
    session: NbaSession, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    get = ScriptedGet(*[_response(502)] * 4)
    monkeypatch.setattr(session.curl, "get", get)

    assert session.get(URL).status_code == 502
    assert len(get.calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_retries_connection_errors_then_reraises(
    session: NbaSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    get = ScriptedGet(*[requests.exceptions.ConnectionError("refused")] * 4)
    monkeypatch.setattr(session.curl, "get", get)

    with pytest.raises(requests.exceptions.ConnectionError):
        session.get(URL)
    assert len(get.calls) == 4


def test_does_not_retry_a_403_or_a_read_timeout(
    session: NbaSession, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    get = ScriptedGet(_response(403), requests.exceptions.ReadTimeout("slow"))
    monkeypatch.setattr(session.curl, "get", get)

    assert session.get(URL).status_code == 403
    with pytest.raises(requests.exceptions.ReadTimeout):
        session.get(URL)
    assert len(get.calls) == 2
    assert sleeps == []
