import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from nba_today.sources import http
from nba_today.sources.espn import (
    fetch_athletes,
    fetch_injuries,
    fetch_postseason,
    fetch_scoreboard,
    fetch_standings,
    get_postseason,
)
from nba_today.sources.http import make_client
from nba_today.sources.models.espn_athletes import EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import EspnScoreboard, Event, is_counted
from nba_today.sources.models.espn_standings import Entry, EspnStandings
from nba_today.sources.snapshot import Payload, SnapshotStore

BASE = "https://site.api.espn.com"
SCOREBOARD_URL = f"{BASE}/apis/site/v2/sports/basketball/nba/scoreboard"
STANDINGS_URL = f"{BASE}/apis/v2/sports/basketball/nba/standings"
INJURIES_URL = f"{BASE}/apis/site/v2/sports/basketball/nba/injuries"
WEB_BASE = "https://site.web.api.espn.com"
ATHLETES_URL = f"{WEB_BASE}/apis/common/v3/sports/basketball/nba/statistics/byathlete"
FIXTURES = Path(__file__).parent / "fixtures" / "espn"

REGULAR_OT = "20251116"
CUP_KNOCKOUT = "20251210"
CUP_FINAL = "20251216"
PLAY_IN = "20260415"
PLAYOFFS = "20260420"
OPENING_NIGHT = "20261020"
LAST_REGULAR_NIGHT = "20260412"
PRESEASON = "20251005"
ALL_STAR = "20260215"


def _payload(night: str) -> Payload:
    return _fixture(f"scoreboard_{night}")


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _events(night: str) -> list[Event]:
    return EspnScoreboard.model_validate(_payload(night)).events


def _no_sleep(seconds: float) -> None:
    pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http.time, "sleep", _no_sleep)


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with make_client(BASE, "nba-today-test/1.0") as c:
        yield c


@pytest.fixture
def web_client() -> Iterator[httpx.Client]:
    with make_client(WEB_BASE, "nba-today-test/1.0") as c:
        yield c


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path)


@respx.mock
def test_today_requests_that_date_and_snapshots_it(
    client: httpx.Client, store: SnapshotStore
) -> None:
    route = respx.get(SCOREBOARD_URL).mock(
        return_value=httpx.Response(200, json=_payload(REGULAR_OT))
    )

    result = fetch_scoreboard(client, store, date(2025, 11, 16), "today")

    assert route.call_count == 1
    assert dict(route.calls.last.request.url.params) == {"dates": "20251116"}
    assert result.stale is False
    assert len(result.data.events) == 5
    assert store.get("espn_scoreboard_today") is not None


@respx.mock
def test_yesterday_requests_the_day_before_under_its_own_snapshot(
    client: httpx.Client, store: SnapshotStore
) -> None:
    route = respx.get(SCOREBOARD_URL).mock(
        return_value=httpx.Response(200, json=_payload(REGULAR_OT))
    )

    fetch_scoreboard(client, store, date(2025, 11, 17), "yesterday")

    assert dict(route.calls.last.request.url.params) == {"dates": "20251116"}
    assert store.get("espn_scoreboard_yesterday") is not None
    assert store.get("espn_scoreboard_today") is None


@respx.mock
def test_failing_request_returns_the_snapshot_as_stale(
    client: httpx.Client, store: SnapshotStore
) -> None:
    snapshot_at = store.put("espn_scoreboard_today", _payload(OPENING_NIGHT))
    respx.get(SCOREBOARD_URL).mock(return_value=httpx.Response(503))

    result = fetch_scoreboard(client, store, date(2026, 10, 20), "today")

    assert result.stale is True
    assert result.fetched_at == snapshot_at
    assert [e.id for e in result.data.events] == ["401909088", "401909089", "401909090"]


def test_no_client_reads_the_snapshot_alone(store: SnapshotStore) -> None:
    store.put("espn_scoreboard_yesterday", _payload(REGULAR_OT))

    result = fetch_scoreboard(None, store, date(2025, 11, 17), "yesterday")

    assert result.stale is True
    assert len(result.data.events) == 5


@respx.mock
def test_postseason_requests_each_month_from_april_and_keeps_only_postseason_games(
    client: httpx.Client, store: SnapshotStore
) -> None:
    april = {"events": _payload(LAST_REGULAR_NIGHT)["events"] + _payload(PLAY_IN)["events"]}
    routes = [
        respx.get(SCOREBOARD_URL, params__contains={"dates": month}).mock(
            return_value=httpx.Response(200, json=payload)
        )
        for month, payload in (("202604", april), ("202605", _payload(PLAYOFFS)))
    ]

    result = fetch_postseason(client, store, date(2026, 5, 10))

    assert [dict(r.calls.last.request.url.params) for r in routes] == [
        {"dates": "202604", "limit": "1000"},
        {"dates": "202605", "limit": "1000"},
    ]
    assert [e.season.type for e in result.data.events if e.season] == [5, 5, 3, 3, 3]
    assert store.get("espn_postseason") is not None


@respx.mock
def test_postseason_before_the_postseason_year_requests_nothing(client: httpx.Client) -> None:
    route = respx.get(SCOREBOARD_URL)

    assert get_postseason(client, date(2026, 10, 20)) == {"events": []}
    assert route.call_count == 0


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("scoreboard_*.json")), ids=lambda p: p.stem)
def test_every_fixture_parses_with_both_sides_identified(path: Path) -> None:
    scoreboard = EspnScoreboard.model_validate(json.loads(path.read_text()))

    assert scoreboard.events
    for event in scoreboard.events:
        assert event.id and event.date and event.date.tzinfo is not None
        [competition] = event.competitions
        assert sorted(c.home_away or "" for c in competition.competitors) == ["away", "home"]
        assert all(c.team and c.team.id and c.team.abbreviation for c in competition.competitors)


def test_final_game_parses_the_fields_spec_5_1_reads() -> None:
    event = _events(REGULAR_OT)[0]
    [competition] = event.competitions
    home = next(c for c in competition.competitors if c.home_away == "home")
    points = next(cat for cat in home.leaders if cat.name == "points")

    assert event.date == datetime(2025, 11, 16, 20, 30, tzinfo=UTC)
    assert event.status and event.status.type
    assert (event.status.type.name, event.status.type.completed) == ("STATUS_FINAL", True)
    assert competition.venue and competition.venue.address
    assert (competition.venue.full_name, competition.venue.address.city) == ("TD Garden", "Boston")
    assert home.team and (home.team.id, home.team.abbreviation) == (2, "BOS")
    assert home.score == 121
    assert [r.summary for r in home.records if r.type == "total"] == ["7-7"]
    assert points.leaders[0].value == 33.0
    assert points.leaders[0].athlete and points.leaders[0].athlete.display_name == "Jaylen Brown"
    assert [(b.market, b.names) for b in competition.broadcasts] == [
        ("home", ["NBC Sports BO"]),
        ("away", ["FanDuel SN SoCal", "NBA League Pass"]),
    ]


def test_overtime_periods_are_parsed() -> None:
    periods = [e.status.period if e.status else None for e in _events(REGULAR_OT)]

    assert periods == [4, 4, 4, 5, 6]


def test_playoff_game_carries_its_series_and_round_note() -> None:
    [competition] = _events(PLAYOFFS)[0].competitions

    assert competition.series and competition.series.summary == "CLE leads series 2-0"
    assert [n.headline for n in competition.notes] == ["East 1st Round - Game 2"]


def test_scheduled_game_has_no_leaders_and_a_national_broadcast() -> None:
    event = _events(OPENING_NIGHT)[0]
    [competition] = event.competitions

    assert event.status and event.status.period == 0
    assert all(c.leaders == [] for c in competition.competitors)
    assert [(b.market, b.names) for b in competition.broadcasts] == [("national", ["NBC"])]


def test_missing_and_blank_fields_default_to_none() -> None:
    scoreboard = EspnScoreboard.model_validate(
        {
            "events": [
                {
                    "id": "1",
                    "date": "2026-10-20T23:00Z",
                    "competitions": [{"competitors": [{"score": "", "team": {"id": "2"}}]}],
                }
            ]
        }
    )

    [event] = scoreboard.events
    [competitor] = event.competitions[0].competitors
    assert (event.season, event.status) == (None, None)
    assert competitor.score is None
    assert competitor.team and competitor.team.id == 2


def test_event_missing_its_id_and_date_still_parses() -> None:
    [event] = EspnScoreboard.model_validate({"events": [{"season": {"type": 2}}]}).events

    assert (event.id, event.date) == (None, None)


def test_empty_scoreboard_parses() -> None:
    assert EspnScoreboard.model_validate({}).events == []


@pytest.mark.parametrize(
    "night", [REGULAR_OT, CUP_KNOCKOUT, CUP_FINAL, PLAY_IN, PLAYOFFS, OPENING_NIGHT]
)
def test_counted_games(night: str) -> None:
    events = _events(night)

    assert events
    assert all(is_counted(e) for e in events)


@pytest.mark.parametrize("night", [PRESEASON, ALL_STAR])
def test_uncounted_games(night: str) -> None:
    events = _events(night)

    assert events
    assert not any(is_counted(e) for e in events)


def test_all_star_fixture_includes_the_championship_game() -> None:
    headlines = {n.headline for e in _events(ALL_STAR) for c in e.competitions for n in c.notes}

    assert headlines == {"NBA All-Star - Round Robin", "NBA All-Star - Championship"}


def test_event_without_a_season_is_not_counted() -> None:
    event = Event.model_validate({"id": "1", "date": "2026-10-20T23:00Z"})

    assert is_counted(event) is False


# Standings, injuries, and athlete stats

FINAL_2025_26 = "standings_2026"
OPENING_2026_27 = "standings_2027"
ATHLETES_2025_26 = "athletes_2026"
INJURIES = "injuries"


def _entries(name: str) -> list[Entry]:
    standings = EspnStandings.model_validate(_fixture(name))
    return [e for c in standings.children if c.standings for e in c.standings.entries]


@pytest.mark.parametrize(
    ("today", "season"), [(date(2026, 4, 12), "2026"), (date(2026, 10, 20), "2027")]
)
@respx.mock
def test_standings_request_the_regular_season_of_todays_season(
    client: httpx.Client, store: SnapshotStore, today: date, season: str
) -> None:
    route = respx.get(STANDINGS_URL).mock(
        return_value=httpx.Response(200, json=_fixture(FINAL_2025_26))
    )

    result = fetch_standings(client, store, today)

    assert dict(route.calls.last.request.url.params) == {"season": season, "seasontype": "2"}
    assert result.stale is False
    assert store.get("espn_standings") is not None


@respx.mock
def test_injuries_send_the_season_and_season_type(
    client: httpx.Client, store: SnapshotStore
) -> None:
    route = respx.get(INJURIES_URL).mock(return_value=httpx.Response(200, json=_fixture(INJURIES)))

    result = fetch_injuries(client, store, date(2026, 10, 20))

    assert dict(route.calls.last.request.url.params) == {"season": "2027", "seasontype": "2"}
    assert result.stale is False
    assert store.get("espn_injuries") is not None


@respx.mock
def test_athletes_request_every_athlete_from_the_web_api(
    web_client: httpx.Client, store: SnapshotStore
) -> None:
    route = respx.get(ATHLETES_URL).mock(
        return_value=httpx.Response(200, json=_fixture(ATHLETES_2025_26))
    )

    result = fetch_athletes(web_client, store, date(2026, 4, 12))

    assert dict(route.calls.last.request.url.params) == {
        "season": "2026",
        "seasontype": "2",
        "isqualified": "false",
        "limit": "1000",
        "sort": "offensive.avgPoints:desc",
    }
    assert len(result.data.athletes) == 42
    assert store.get("espn_athletes") == (_fixture(ATHLETES_2025_26), result.fetched_at)


@respx.mock
def test_failing_standings_fall_back_to_the_snapshot(
    client: httpx.Client, store: SnapshotStore
) -> None:
    snapshot_at = store.put("espn_standings", _fixture(FINAL_2025_26))
    respx.get(STANDINGS_URL).mock(return_value=httpx.Response(503))

    result = fetch_standings(client, store, date(2026, 4, 12))

    assert (result.stale, result.fetched_at) == (True, snapshot_at)
    assert len(_entries(FINAL_2025_26)) == 30


def test_no_client_reads_each_snapshot_alone(store: SnapshotStore) -> None:
    store.put("espn_standings", _fixture(OPENING_2026_27))
    store.put("espn_injuries", _fixture(INJURIES))
    store.put("espn_athletes", _fixture(ATHLETES_2025_26))
    today = date(2026, 10, 20)

    results = [
        fetch_standings(None, store, today),
        fetch_injuries(None, store, today),
        fetch_athletes(None, store, today),
    ]

    assert all(r.stale for r in results)


def test_final_standings_parse_every_team_in_two_conferences() -> None:
    standings = EspnStandings.model_validate(_fixture(FINAL_2025_26))

    assert [
        (c.abbreviation, len(c.standings.entries if c.standings else []))
        for c in standings.children
    ] == [("East", 15), ("West", 15)]
    for entry in _entries(FINAL_2025_26):
        assert entry.team and entry.team.id and entry.team.abbreviation
        assert entry.wins is not None and entry.losses is not None
        assert entry.wins + entry.losses == 82
        assert entry.playoff_seed and entry.win_percent is not None
        assert entry.games_behind is not None
        assert entry.streak and entry.last_ten_games and entry.clincher


def test_final_standings_carry_every_clinch_code() -> None:
    clinchers = {e.clincher for e in _entries(FINAL_2025_26)}

    assert clinchers == {"z", "y", "x", "xp", "pb", "e", "*"}


def test_final_standings_entry_flattens_its_stats_by_name() -> None:
    detroit = next(e for e in _entries(FINAL_2025_26) if e.team and e.team.abbreviation == "DET")

    assert detroit.model_dump(exclude={"team"}) == {
        "wins": 60,
        "losses": 22,
        "win_percent": pytest.approx(0.7317, abs=1e-4),
        "games_behind": 0.0,
        "playoff_seed": 1,
        "streak": "W3",
        "clincher": "z",
        "last_ten_games": "8-2",
    }


def test_final_standings_entries_are_not_in_seed_order() -> None:
    seeds = [e.playoff_seed or 0 for e in _entries(FINAL_2025_26)]

    assert seeds != sorted(seeds)
    assert sorted(seeds[:15]) == sorted(seeds[15:]) == list(range(1, 16))


def test_opening_day_standings_have_no_clinch_or_last_ten() -> None:
    entries = _entries(OPENING_2026_27)

    assert len(entries) == 30
    assert all((e.wins, e.losses, e.playoff_seed) == (0, 0, 1) for e in entries)
    assert all(e.clincher is None and e.last_ten_games is None for e in entries)


def test_standings_entry_tolerates_missing_and_string_stats() -> None:
    entry = Entry.model_validate(
        {
            "team": {"id": "8", "abbreviation": "DET"},
            "stats": [
                {"name": "wins", "value": "60"},
                {"name": "losses", "value": ""},
                {"name": "streak", "value": 3.0, "displayValue": "W3"},
                {"value": 1.0},
                "junk",
            ],
        }
    )

    assert entry.team and entry.team.id == 8
    assert (entry.wins, entry.losses, entry.streak, entry.playoff_seed) == (60, None, "W3", None)


def test_standings_without_stats_parse() -> None:
    assert Entry.model_validate({}).wins is None
    assert EspnStandings.model_validate({}).children == []


def test_injuries_parse_status_name_and_team_for_every_player() -> None:
    report = EspnInjuries.model_validate(_fixture(INJURIES))
    injuries = [i for t in report.injuries for i in t.injuries]

    assert {i.status for i in injuries} == {"Out", "Day-To-Day"}
    for injury in injuries:
        assert injury.athlete and injury.athlete.display_name
        assert injury.athlete.team and injury.athlete.team.abbreviation


def test_injury_can_be_listed_under_a_team_the_player_is_not_on() -> None:
    report = EspnInjuries.model_validate(_fixture(INJURIES))
    clippers = next(t for t in report.injuries if t.id == 12)
    ingram = next(
        i for i in clippers.injuries if i.athlete and i.athlete.display_name == "Brandon Ingram"
    )

    assert clippers.display_name == "LA Clippers"
    assert ingram.athlete and ingram.athlete.team
    assert (ingram.athlete.team.id, ingram.athlete.team.abbreviation) == (28, "TOR")


def test_athletes_parse_gp_mpg_ppg_rpg_apg_and_team_for_every_row() -> None:
    athletes = EspnAthletes.model_validate(_fixture(ATHLETES_2025_26)).athletes

    assert len(athletes) == 42
    for row in athletes:
        assert row.athlete and row.athlete.display_name
        assert row.athlete.team_id and row.athlete.team_short_name
        assert row.general and row.offensive
        assert row.general.games_played and row.general.avg_minutes is not None
        assert row.general.avg_rebounds is not None
        assert row.offensive.avg_points is not None and row.offensive.avg_assists is not None


def test_athlete_values_are_named_by_the_top_level_categories() -> None:
    [first, *_] = EspnAthletes.model_validate(_fixture(ATHLETES_2025_26)).athletes

    assert first.athlete and first.general and first.offensive
    assert (first.athlete.display_name, first.athlete.team_id) == ("Shai Gilgeous-Alexander", 25)
    assert first.athlete.team_short_name == "OKC"
    assert first.general.games_played == 68
    assert first.general.avg_minutes == pytest.approx(33.22, abs=0.01)
    assert first.general.avg_rebounds == pytest.approx(4.29, abs=0.01)
    assert first.offensive.avg_points == pytest.approx(31.13, abs=0.01)
    assert first.offensive.avg_assists == pytest.approx(6.59, abs=0.01)


def test_athletes_report_the_season_espn_answered_for() -> None:
    athletes = EspnAthletes.model_validate(_fixture(ATHLETES_2025_26))

    assert athletes.requested_season and athletes.requested_season.year == 2026


def test_athlete_with_missing_categories_parses() -> None:
    athletes = EspnAthletes.model_validate(
        {
            "categories": [{"name": "general", "names": ["gamesPlayed"]}],
            "athletes": [
                {"athlete": {"displayName": "A", "teamId": "13"}, "categories": []},
                {"athlete": {}, "categories": [{"name": "general", "values": [5.0]}]},
                {"categories": [{"name": "unknown", "values": [1.0]}]},
            ],
        }
    )

    assert [a.general.games_played if a.general else None for a in athletes.athletes] == [
        None,
        5,
        None,
    ]
    assert athletes.athletes[0].athlete and athletes.athletes[0].athlete.team_id == 13


def test_empty_athletes_parse() -> None:
    assert EspnAthletes.model_validate({}).athletes == []
