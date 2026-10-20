"""NBA.com client: the season schedule, and the stats.nba.com team and player dashboards (§5.2).

The schedule gives local TV, streaming, and game labels; the dashboards give team ratings and the
PIE leaderboard.

NBA.com is enrichment (ADR-0004): with `NBA_ENRICHMENT=false` every fetch returns None without a
request, and the digest treats the source as unavailable. Each function takes any object with a
`get(url, params)` method, so tests pass a fake: respx cannot intercept curl_cffi. Production
passes `nba_http.make_nba_session()`.

The schedule is one static file for the whole season, and takes no season parameter. A file
labelled with any season other than the one `today` falls in does not parse, so it never
replaces a good snapshot: around the August rollover the CDN may still serve the old season.

The dashboards take the season label explicitly (§4.2) and answer `resultSets[0].{headers,
rowSet}`. stats.nba.com rejects a request that leaves out some of its parameters, so each one
sends the full set nba_api 1.11.4 sends, blanks included. It accepts a request without the others
it echoes back (`GameSubtype`, `ISTRound`, and for players `ActiveRoster`).
"""

from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel

from nba_today.season import season_label
from nba_today.settings import Settings
from nba_today.sources.fetched import Fetched, fetch_or_snapshot
from nba_today.sources.models.nba_player_stats import PlayerAdvanced, PlayerBase
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.models.nba_team_stats import TeamAdvanced
from nba_today.sources.snapshot import Payload, SnapshotStore

SCHEDULE_PATH = "/static/json/staticData/scheduleLeagueV2_1.json"
SCHEDULE_SOURCE = "nba_schedule"

TEAM_STATS_PATH = "/stats/leaguedashteamstats"
PLAYER_STATS_PATH = "/stats/leaguedashplayerstats"
TEAM_ADVANCED_SOURCE = "nba_team_advanced"
PLAYER_ADVANCED_SOURCE = "nba_player_advanced"
PLAYER_BASE_SOURCE = "nba_player_base"
REGULAR_SEASON = "Regular Season"

# The parameters nba_api sends to `leaguedashteamstats`, less `Season` and `MeasureType`.
TEAM_STATS_PARAMS = {
    "Conference": "",
    "DateFrom": "",
    "DateTo": "",
    "Division": "",
    "GameScope": "",
    "GameSegment": "",
    "LastNGames": "0",
    "LeagueID": "00",
    "Location": "",
    "Month": "0",
    "OpponentTeamID": "0",
    "Outcome": "",
    "PORound": "",
    "PaceAdjust": "N",
    "PerMode": "PerGame",
    "Period": "0",
    "PlayerExperience": "",
    "PlayerPosition": "",
    "PlusMinus": "N",
    "Rank": "N",
    "SeasonSegment": "",
    "SeasonType": REGULAR_SEASON,
    "ShotClockRange": "",
    "StarterBench": "",
    "TeamID": "",
    "TwoWay": "",
    "VsConference": "",
    "VsDivision": "",
}
# `leaguedashplayerstats` takes the same, plus the player-attribute filters.
PLAYER_STATS_PARAMS = TEAM_STATS_PARAMS | {
    "College": "",
    "Country": "",
    "DraftPick": "",
    "DraftYear": "",
    "Height": "",
    "Weight": "",
}


class Response(Protocol):
    def raise_for_status(self) -> object: ...

    def json(self) -> Any: ...


class Session(Protocol):
    def get(self, url: str, params: dict[str, str] | None = None) -> Response: ...


class WrongSeason(ValueError):
    def __init__(self, expected: str, got: str | None) -> None:
        super().__init__(f"schedule is for season {got}, expected {expected}")


def _get(session: Session, url: str, params: dict[str, str] | None = None) -> Payload:
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()


def get_schedule(session: Session, cdn_base: str) -> Payload:
    """The raw season schedule, about 7.5 MB; raises on an error status."""
    return _get(session, f"{cdn_base}{SCHEDULE_PATH}")


def parse_schedule(payload: Payload, today: date) -> NbaSchedule:
    """Parse the schedule; raise `WrongSeason` unless it is for the season `today` falls in."""
    schedule = NbaSchedule.model_validate(payload)
    expected = season_label(today)
    got = schedule.league_schedule.season_year if schedule.league_schedule else None
    if got != expected:
        raise WrongSeason(expected, got)
    return schedule


def fetch_schedule(
    session: Session | None, store: SnapshotStore, settings: Settings, today: date
) -> Fetched[NbaSchedule] | None:
    """The season schedule, or its snapshot; None when enrichment is off.

    With no session, the snapshot alone (`preview --from-snapshots`).
    """
    if not settings.nba_enrichment:
        return None
    fetch = None if session is None else lambda: get_schedule(session, settings.nba_cdn_base)
    return fetch_or_snapshot(
        SCHEDULE_SOURCE, fetch, lambda payload: parse_schedule(payload, today), store
    )


def _stats_params(today: date, measure_type: str) -> dict[str, str]:
    return {"Season": season_label(today), "MeasureType": measure_type}


def get_team_advanced(session: Session, stats_base: str, today: date) -> Payload:
    """The raw per-game advanced team dashboard for the regular season `today` falls in."""
    params = TEAM_STATS_PARAMS | _stats_params(today, "Advanced")
    return _get(session, f"{stats_base}{TEAM_STATS_PATH}", params)


def get_player_advanced(session: Session, stats_base: str, today: date) -> Payload:
    """The raw per-game advanced player dashboard for the regular season `today` falls in."""
    params = PLAYER_STATS_PARAMS | _stats_params(today, "Advanced")
    return _get(session, f"{stats_base}{PLAYER_STATS_PATH}", params)


def get_player_base(session: Session, stats_base: str, today: date) -> Payload:
    """The raw per-game base player dashboard for the regular season `today` falls in."""
    params = PLAYER_STATS_PARAMS | _stats_params(today, "Base")
    return _get(session, f"{stats_base}{PLAYER_STATS_PATH}", params)


def parse_result_set(payload: Payload) -> list[dict[str, Any]]:
    """Each row of `resultSets[0]` as a dict keyed by its headers.

    Raises on a payload of any other shape, or a row whose length does not match the headers, so
    a malformed response never replaces a good snapshot.
    """
    result_set = payload["resultSets"][0]
    headers: list[str] = result_set["headers"]
    return [dict(zip(headers, row, strict=True)) for row in result_set["rowSet"]]


def _parse_rows[M: BaseModel](model: type[M]) -> Callable[[Payload], list[M]]:
    return lambda payload: [model.model_validate(row) for row in parse_result_set(payload)]


def _fetch_stats[M: BaseModel](
    source: str,
    get: Callable[[Session, str, date], Payload],
    model: type[M],
    session: Session | None,
    store: SnapshotStore,
    settings: Settings,
    today: date,
) -> Fetched[list[M]] | None:
    if not settings.nba_enrichment:
        return None
    fetch = None if session is None else lambda: get(session, settings.nba_stats_base, today)
    return fetch_or_snapshot(source, fetch, _parse_rows(model), store)


def fetch_team_advanced(
    session: Session | None, store: SnapshotStore, settings: Settings, today: date
) -> Fetched[list[TeamAdvanced]] | None:
    """Every team's advanced line, or its snapshot; None when enrichment is off."""
    return _fetch_stats(
        TEAM_ADVANCED_SOURCE, get_team_advanced, TeamAdvanced, session, store, settings, today
    )


def fetch_player_advanced(
    session: Session | None, store: SnapshotStore, settings: Settings, today: date
) -> Fetched[list[PlayerAdvanced]] | None:
    """Every player's advanced line, or its snapshot; None when enrichment is off."""
    return _fetch_stats(
        PLAYER_ADVANCED_SOURCE, get_player_advanced, PlayerAdvanced, session, store, settings, today
    )


def fetch_player_base(
    session: Session | None, store: SnapshotStore, settings: Settings, today: date
) -> Fetched[list[PlayerBase]] | None:
    """Every player's base line, or its snapshot; None when enrichment is off."""
    return _fetch_stats(
        PLAYER_BASE_SOURCE, get_player_base, PlayerBase, session, store, settings, today
    )
