"""Every source the pipeline fetches, answered from the recorded fixtures: ESPN as respx routes,
NBA.com as a fake session, or all of them seeded into a snapshot store.

The digest date is `FULL_DAY`: yesterday's scoreboard is the recorded 2025-11-16 slate, and
today's is the same slate moved forward a day (see `tests/bundles.py`).
"""

from typing import Any, Self

import httpx
import pytest
import respx

from nba_today.sources.snapshot import Payload, SnapshotStore
from tests.bundles import a_day_later, fixture

ESPN = "https://site.api.espn.com"
ESPN_WEB = "https://site.web.api.espn.com"
NBA_CDN = "https://cdn.nba.com"
NBA_STATS = "https://stats.nba.com"

SEASON = "2025-26"

# Snapshot source name → its fixture payload.
PAYLOADS: dict[str, Payload] = {
    "espn_scoreboard_today": a_day_later(fixture("espn/scoreboard_20251116")),
    "espn_scoreboard_yesterday": fixture("espn/scoreboard_20251116"),
    "espn_standings": fixture("espn/standings_2026"),
    "espn_injuries": fixture("espn/injuries"),
    "espn_athletes": fixture("espn/athletes_2026"),
    "nba_schedule": fixture(f"nba/schedule_{SEASON}"),
    "nba_team_advanced": fixture(f"nba/team_advanced_{SEASON}"),
    "nba_player_advanced": fixture(f"nba/player_advanced_{SEASON}"),
    "nba_player_base": fixture(f"nba/player_base_{SEASON}"),
}
SOURCES = list(PAYLOADS)
NBA_SOURCES = [source for source in SOURCES if source.startswith("nba_")]


def seed_snapshots(store: SnapshotStore) -> None:
    for source, payload in PAYLOADS.items():
        store.put(source, payload)


def mock_espn(router: respx.MockRouter) -> dict[str, respx.Route]:
    """One route per ESPN source, keyed by its snapshot name; set `return_value` to fail one."""

    def ok(source: str) -> httpx.Response:
        return httpx.Response(200, json=PAYLOADS[source])

    scoreboard = f"{ESPN}/apis/site/v2/sports/basketball/nba/scoreboard"
    return {
        "espn_scoreboard_today": router.get(
            scoreboard, params__contains={"dates": "20251117"}
        ).mock(return_value=ok("espn_scoreboard_today")),
        "espn_scoreboard_yesterday": router.get(
            scoreboard, params__contains={"dates": "20251116"}
        ).mock(return_value=ok("espn_scoreboard_yesterday")),
        "espn_standings": router.get(f"{ESPN}/apis/v2/sports/basketball/nba/standings").mock(
            return_value=ok("espn_standings")
        ),
        "espn_injuries": router.get(f"{ESPN}/apis/site/v2/sports/basketball/nba/injuries").mock(
            return_value=ok("espn_injuries")
        ),
        "espn_athletes": router.get(
            f"{ESPN_WEB}/apis/common/v3/sports/basketball/nba/statistics/byathlete"
        ).mock(return_value=ok("espn_athletes")),
    }


class FakeNbaResponse:
    def __init__(self, body: Payload) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self._body


class FakeNbaSession:
    """Stands in for `NbaSession`, answering each NBA.com request from the fixtures.

    With `fail`, every request raises instead, as a blocked or unreachable NBA.com would.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get(self, url: str, params: dict[str, str] | None = None) -> FakeNbaResponse:
        self.calls.append((url, params))
        if self.fail:
            raise ConnectionError(f"NBA.com unreachable: {url}")
        return FakeNbaResponse(PAYLOADS[_nba_source(url, params or {})])

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass


def _nba_source(url: str, params: dict[str, str]) -> str:
    if url == f"{NBA_CDN}/static/json/staticData/scheduleLeagueV2_1.json":
        return "nba_schedule"
    if url == f"{NBA_STATS}/stats/leaguedashteamstats":
        return "nba_team_advanced"
    if url == f"{NBA_STATS}/stats/leaguedashplayerstats":
        return f"nba_player_{params['MeasureType'].lower()}"
    raise AssertionError(f"unexpected NBA.com request: {url}")


def use_nba_session(monkeypatch: pytest.MonkeyPatch, session: FakeNbaSession) -> None:
    """Make the pipeline open `session` in place of a real curl_cffi session."""
    monkeypatch.setattr("nba_today.pipeline.make_nba_session", lambda: session)
