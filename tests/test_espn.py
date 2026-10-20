import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from nba_today.sources import http
from nba_today.sources.espn import fetch_scoreboard, is_counted
from nba_today.sources.http import make_client
from nba_today.sources.models.espn_scoreboard import EspnScoreboard, Event
from nba_today.sources.snapshot import Payload, SnapshotStore

BASE = "https://site.api.espn.com"
SCOREBOARD_URL = f"{BASE}/apis/site/v2/sports/basketball/nba/scoreboard"
FIXTURES = Path(__file__).parent / "fixtures" / "espn"

REGULAR_OT = "20251116"
CUP_KNOCKOUT = "20251210"
CUP_FINAL = "20251216"
PLAY_IN = "20260415"
PLAYOFFS = "20260420"
OPENING_NIGHT = "20261020"
PRESEASON = "20251005"
ALL_STAR = "20260215"


def _payload(night: str) -> Payload:
    return json.loads((FIXTURES / f"scoreboard_{night}.json").read_text())


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
