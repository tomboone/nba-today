import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from nba_today.settings import Settings
from nba_today.sources.fetched import Fetched, SourceUnavailable
from nba_today.sources.models.nba_schedule import Game, NbaSchedule
from nba_today.sources.models.nba_stats import PlayerAdvanced, PlayerBase, TeamAdvanced
from nba_today.sources.nba import (
    fetch_player_advanced,
    fetch_player_base,
    fetch_schedule,
    fetch_team_advanced,
    parse_result_set,
)
from nba_today.sources.snapshot import Payload, SnapshotStore

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
FIXTURES = Path(__file__).parent / "fixtures" / "nba"

SEASON_2025_26 = "schedule_2025-26"
SEASON_2026_27 = "schedule_2026-27"
OPENING_NIGHT = date(2026, 10, 20)


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _games(name: str, day: date) -> list[Game]:
    schedule = NbaSchedule.model_validate(_fixture(name))
    assert schedule.league_schedule
    return next(d.games for d in schedule.league_schedule.game_dates if d.game_date == day)


def _game(name: str, day: date, away: str, home: str) -> Game:
    return next(
        g
        for g in _games(name, day)
        if g.away_team
        and g.home_team
        and (g.away_team.team_tricode, g.home_team.team_tricode) == (away, home)
    )


def _displays(game: Game) -> dict[str, list[str | None]]:
    assert game.broadcasters
    return {
        name: [b.broadcaster_display for b in broadcasters]
        for name, broadcasters in game.broadcasters
        if broadcasters
    }


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._body


class FakeSession:
    """Stands in for `NbaSession`: records each request and returns one canned response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get(self, url: str, params: dict[str, str] | None = None) -> FakeResponse:
        self.calls.append((url, params))
        return self.response


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path)


@pytest.fixture
def settings() -> Settings:
    return Settings(recipients=["a@example.com"])


def test_schedule_requests_the_cdn_file_and_snapshots_it(
    store: SnapshotStore, settings: Settings
) -> None:
    session = FakeSession(FakeResponse(200, _fixture(SEASON_2026_27)))

    result = fetch_schedule(session, store, settings, OPENING_NIGHT)

    assert session.calls == [(SCHEDULE_URL, None)]
    assert result and result.stale is False
    assert store.get("nba_schedule") == (_fixture(SEASON_2026_27), result.fetched_at)


def test_enrichment_off_makes_no_request(store: SnapshotStore) -> None:
    store.put("nba_schedule", _fixture(SEASON_2026_27))
    session = FakeSession(FakeResponse(200, _fixture(SEASON_2026_27)))
    settings = Settings(recipients=["a@example.com"], nba_enrichment=False)

    assert fetch_schedule(session, store, settings, OPENING_NIGHT) is None
    assert fetch_schedule(None, store, settings, OPENING_NIGHT) is None
    assert session.calls == []


def test_failing_request_returns_the_snapshot_as_stale(
    store: SnapshotStore, settings: Settings
) -> None:
    snapshot_at = store.put("nba_schedule", _fixture(SEASON_2026_27))
    session = FakeSession(FakeResponse(403, "<html>Access Denied</html>"))

    result = fetch_schedule(session, store, settings, OPENING_NIGHT)

    assert result and (result.stale, result.fetched_at) == (True, snapshot_at)


def test_no_session_reads_the_snapshot_alone(store: SnapshotStore, settings: Settings) -> None:
    store.put("nba_schedule", _fixture(SEASON_2026_27))

    result = fetch_schedule(None, store, settings, OPENING_NIGHT)

    assert result and result.stale is True


def test_last_seasons_schedule_never_replaces_the_snapshot(
    store: SnapshotStore, settings: Settings
) -> None:
    snapshot_at = store.put("nba_schedule", _fixture(SEASON_2026_27))
    session = FakeSession(FakeResponse(200, _fixture(SEASON_2025_26)))

    result = fetch_schedule(session, store, settings, OPENING_NIGHT)

    assert result and (result.stale, result.fetched_at) == (True, snapshot_at)
    assert store.get("nba_schedule") == (_fixture(SEASON_2026_27), snapshot_at)


def test_last_seasons_snapshot_is_unavailable(store: SnapshotStore, settings: Settings) -> None:
    store.put("nba_schedule", _fixture(SEASON_2025_26))

    with pytest.raises(SourceUnavailable):
        fetch_schedule(None, store, settings, OPENING_NIGHT)


@pytest.mark.parametrize("name", [SEASON_2025_26, SEASON_2026_27])
def test_every_fixture_game_parses_with_both_teams_and_a_tip_off(name: str) -> None:
    schedule = NbaSchedule.model_validate(_fixture(name))

    assert schedule.league_schedule and schedule.league_schedule.season_year == name[-7:]
    for game_date in schedule.league_schedule.game_dates:
        assert game_date.game_date and game_date.games
        for game in game_date.games:
            assert game.game_id and game.broadcasters
            assert game.game_date_time_utc and game.game_date_time_utc.tzinfo is not None
            assert game.home_team and game.home_team.team_tricode
            assert game.away_team and game.away_team.team_tricode


def test_regular_night_has_home_and_away_tv_and_ott_and_no_label() -> None:
    game = _game(SEASON_2026_27, date(2026, 10, 21), "MIL", "WAS")

    assert game.game_date_time_utc == datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
    assert (game.game_label, game.game_sub_label, game.series_text) == (None, None, None)
    assert _displays(game) == {
        "home_tv_broadcasters": ["MNMT/Monumental+"],
        "away_tv_broadcasters": ["MY24"],
        "away_ott_broadcasters": ["Bucks+"],
    }


def test_home_ott_and_several_tv_broadcasters_keep_source_order() -> None:
    game = _game(SEASON_2026_27, date(2026, 10, 21), "PHX", "POR")

    assert _displays(game) == {
        "home_tv_broadcasters": ["KUNP"],
        "home_ott_broadcasters": ["BlazerVision"],
        "away_tv_broadcasters": ["3TV / AZFS", "3TV/AZFS/Suns+ on AZFS App"],
        "away_ott_broadcasters": ["Suns+"],
    }


def test_opening_night_lists_national_streaming_under_tv() -> None:
    game = _game(SEASON_2026_27, OPENING_NIGHT, "BOS", "DET")

    assert _displays(game) == {"national_tv_broadcasters": ["NBC", "Peacock"]}


def test_national_ott_parses() -> None:
    # No game in the 2025-26 or 2026-27 schedule fills this list (checked 2026-10-20): Peacock
    # and Prime Video come under national TV. It is parsed in case that changes.
    game = Game.model_validate(
        {"broadcasters": {"nationalOttBroadcasters": [{"broadcasterDisplay": "Peacock"}]}}
    )

    assert _displays(game) == {"national_ott_broadcasters": ["Peacock"]}


def test_game_late_in_utc_is_listed_under_its_eastern_date() -> None:
    game = _game(SEASON_2025_26, date(2025, 11, 16), "ATL", "PHX")

    assert game.game_date_time_utc == datetime(2025, 11, 17, 1, 0, tzinfo=UTC)


def test_cup_knockout_carries_its_label() -> None:
    game = _game(SEASON_2025_26, date(2025, 12, 10), "PHX", "OKC")

    assert (game.game_label, game.game_sub_label) == ("Emirates NBA Cup", "West Quarterfinal")
    assert _displays(game) == {
        "national_tv_broadcasters": ["Prime Video"],
        "home_tv_broadcasters": ["FanDuel Sports Network - Oklahoma"],
    }


def test_play_in_carries_its_label_and_outcome() -> None:
    game = _game(SEASON_2025_26, date(2026, 4, 15), "ORL", "PHI")

    assert (game.game_label, game.game_sub_label) == ("SoFi Play-In Tournament", "East")
    assert game.series_text == "PHI clinch 7 seed"


def test_playoff_game_carries_its_round_and_series() -> None:
    game = _game(SEASON_2025_26, date(2026, 4, 20), "TOR", "CLE")

    assert (game.game_label, game.game_sub_label) == ("East First Round", "Game 2")
    assert (game.series_text, game.if_necessary) == ("CLE leads 2-0", False)
    assert _displays(game) == {
        "national_tv_broadcasters": ["NBC Sports Network", "Peacock"],
        "away_tv_broadcasters": ["TSN"],
    }


def test_missing_and_blank_fields_default_to_none() -> None:
    schedule = NbaSchedule.model_validate(
        {
            "leagueSchedule": {
                "gameDates": [
                    {"gameDate": "", "games": [{"gameLabel": "", "broadcasters": {}}]},
                    {},
                ]
            }
        }
    )

    assert schedule.league_schedule
    first, second = schedule.league_schedule.game_dates
    [game] = first.games
    assert (first.game_date, second.game_date, second.games) == (None, None, [])
    assert (game.game_label, game.game_date_time_utc, game.home_team) == (None, None, None)
    assert game.broadcasters and game.broadcasters.home_tv_broadcasters == []


def test_empty_schedule_parses() -> None:
    assert NbaSchedule.model_validate({}).league_schedule is None


STATS_BASE = "https://stats.nba.com/stats"

type FetchStats = Callable[
    [FakeSession | None, SnapshotStore, Settings, date], Fetched[list[Any]] | None
]

# (fetch function, snapshot name, fixture stem, endpoint, MeasureType)
STATS_FETCHES: list[tuple[FetchStats, str, str, str, str]] = [
    (
        fetch_team_advanced,
        "nba_team_advanced",
        "team_advanced",
        "leaguedashteamstats",
        "Advanced",
    ),
    (
        fetch_player_advanced,
        "nba_player_advanced",
        "player_advanced",
        "leaguedashplayerstats",
        "Advanced",
    ),
    (fetch_player_base, "nba_player_base", "player_base", "leaguedashplayerstats", "Base"),
]
STATS_IDS = ["team_advanced", "player_advanced", "player_base"]


def _rows(name: str) -> list[list[Any]]:
    return _fixture(name)["resultSets"][0]["rowSet"]


@pytest.mark.parametrize(
    ("fetch", "source", "stem", "endpoint", "measure"), STATS_FETCHES, ids=STATS_IDS
)
def test_stats_request_the_season_and_measure_and_snapshot_it(
    store: SnapshotStore,
    settings: Settings,
    fetch: FetchStats,
    source: str,
    stem: str,
    endpoint: str,
    measure: str,
) -> None:
    payload = _fixture(f"{stem}_2025-26")
    session = FakeSession(FakeResponse(200, payload))

    result = fetch(session, store, settings, OPENING_NIGHT)

    [(url, params)] = session.calls
    assert url == f"{STATS_BASE}/{endpoint}"
    assert params is not None
    assert (params["Season"], params["MeasureType"], params["SeasonType"]) == (
        "2026-27",
        measure,
        "Regular Season",
    )
    assert (params["PerMode"], params["LeagueID"]) == ("PerGame", "00")
    assert result and result.stale is False and len(result.data) == len(_rows(f"{stem}_2025-26"))
    assert store.get(source) == (payload, result.fetched_at)


@pytest.mark.parametrize("endpoint", ["leaguedashteamstats", "leaguedashplayerstats"])
def test_stats_send_every_parameter_nba_api_sends(
    store: SnapshotStore, settings: Settings, endpoint: str
) -> None:
    # stats.nba.com rejects a request missing any of these, blank or not (nba_api 1.11.4).
    team = {
        "Conference", "DateFrom", "DateTo", "Division", "GameScope", "GameSegment", "LastNGames",
        "LeagueID", "Location", "MeasureType", "Month", "OpponentTeamID", "Outcome", "PORound",
        "PaceAdjust", "PerMode", "Period", "PlayerExperience", "PlayerPosition", "PlusMinus",
        "Rank", "Season", "SeasonSegment", "SeasonType", "ShotClockRange", "StarterBench",
        "TeamID", "TwoWay", "VsConference", "VsDivision",
    }  # fmt: skip
    player = team | {"College", "Country", "DraftPick", "DraftYear", "Height", "Weight"}
    fetch = fetch_team_advanced if endpoint == "leaguedashteamstats" else fetch_player_base
    session = FakeSession(FakeResponse(200, _fixture("player_base_2026-27")))

    fetch(session, store, settings, OPENING_NIGHT)

    [(_, params)] = session.calls
    assert params is not None
    assert set(params) == (team if endpoint == "leaguedashteamstats" else player)


def test_enrichment_off_skips_every_stats_fetch(store: SnapshotStore) -> None:
    settings = Settings(recipients=["a@example.com"], nba_enrichment=False)
    session = FakeSession(FakeResponse(200, _fixture("team_advanced_2025-26")))

    for fetch, source, stem, *_ in STATS_FETCHES:
        store.put(source, _fixture(f"{stem}_2025-26"))
        assert fetch(session, store, settings, OPENING_NIGHT) is None
        assert fetch(None, store, settings, OPENING_NIGHT) is None
    assert session.calls == []


@pytest.mark.parametrize(
    ("fetch", "source", "stem", "endpoint", "measure"), STATS_FETCHES, ids=STATS_IDS
)
def test_failing_stats_request_returns_the_snapshot_as_stale(
    store: SnapshotStore,
    settings: Settings,
    fetch: FetchStats,
    source: str,
    stem: str,
    endpoint: str,
    measure: str,
) -> None:
    snapshot_at = store.put(source, _fixture(f"{stem}_2025-26"))
    session = FakeSession(FakeResponse(403, "<html>Access Denied</html>"))

    result = fetch(session, store, settings, OPENING_NIGHT)

    assert result and (result.stale, result.fetched_at) == (True, snapshot_at)
    assert len(result.data) == len(_rows(f"{stem}_2025-26"))


def test_malformed_stats_never_replace_the_snapshot(
    store: SnapshotStore, settings: Settings
) -> None:
    good = _fixture("team_advanced_2025-26")
    snapshot_at = store.put("nba_team_advanced", good)
    short_row = _fixture("team_advanced_2025-26")
    short_row["resultSets"][0]["rowSet"][0].pop()
    for bad in ({"resource": "leaguedashteamstats"}, short_row):
        session = FakeSession(FakeResponse(200, bad))

        result = fetch_team_advanced(session, store, settings, OPENING_NIGHT)

        assert result and (result.stale, result.fetched_at) == (True, snapshot_at)
    assert store.get("nba_team_advanced") == (good, snapshot_at)


def test_parse_result_set_zips_each_row_with_the_headers() -> None:
    payload = _fixture("player_advanced_2025-26")

    rows = parse_result_set(payload)

    assert len(rows) == len(_rows("player_advanced_2025-26")) == 42
    assert all(isinstance(row["PIE"], float) for row in rows)
    assert list(rows[0]) == payload["resultSets"][0]["headers"]


def test_team_fixture_has_every_team_with_ratings() -> None:
    teams = [
        TeamAdvanced.model_validate(r) for r in parse_result_set(_fixture("team_advanced_2025-26"))
    ]

    assert len(teams) == len({t.team_id for t in teams}) == 30
    for team in teams:
        assert team.gp == 82
        assert team.off_rating and team.def_rating and team.pace
        assert team.net_rating is not None
    hawks = next(t for t in teams if t.team_id == 1610612737)
    assert (hawks.off_rating, hawks.def_rating, hawks.net_rating, hawks.pace) == (
        115.0,
        112.9,
        2.2,
        102.5,
    )


def test_player_fixtures_hold_the_same_players_with_every_field() -> None:
    advanced = [
        PlayerAdvanced.model_validate(r)
        for r in parse_result_set(_fixture("player_advanced_2025-26"))
    ]
    base = [PlayerBase.model_validate(r) for r in parse_result_set(_fixture("player_base_2025-26"))]

    assert {p.player_id for p in advanced} == {p.player_id for p in base}
    for player in advanced:
        assert player.player_id and player.player_name and player.team_id
        assert player.gp and player.min and player.pie is not None and player.ts_pct
    for player in base:
        assert player.pts and player.reb is not None and player.ast is not None


@pytest.mark.parametrize("stem", ["team_advanced", "player_advanced", "player_base"])
def test_season_with_no_games_final_has_no_rows(stem: str) -> None:
    payload = _fixture(f"{stem}_2026-27")

    assert payload["resultSets"][0]["headers"]
    assert parse_result_set(payload) == []


def test_stats_rows_tolerate_blank_null_and_string_numbers() -> None:
    player = PlayerAdvanced.model_validate(
        {"PLAYER_ID": "1630639", "GP": "", "MIN": "9.4", "PIE": None, "OTHER": 1}
    )

    assert (player.player_id, player.gp, player.min, player.pie, player.ts_pct) == (
        1630639,
        None,
        9.4,
        None,
        None,
    )
