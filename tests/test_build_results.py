import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from nba_today.digest.build import build_results
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import GameLeaders, Leader, Result, ResultSide
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.snapshot import Payload

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

REGULAR_NIGHT = date(2025, 11, 16)
CUP_KNOCKOUT = date(2025, 12, 10)
PRESEASON = date(2025, 10, 5)
ALL_STAR = date(2026, 2, 15)


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _scoreboard_payload(name: str) -> Payload:
    return _fixture(f"espn/scoreboard_{name}")


def _bundle(scoreboard: Payload, schedule: Payload | None = None) -> SourceBundle:
    return SourceBundle(
        espn_scoreboard_yesterday=_fresh(EspnScoreboard.model_validate(scoreboard)),
        nba_schedule=None if schedule is None else _fresh(NbaSchedule.model_validate(schedule)),
    )


def _fresh[T](data: T) -> Fetched[T]:
    return Fetched(data, FETCHED_AT, stale=False)


def _results_for(night: date, scoreboard: Payload | None = None) -> list[Result]:
    """The results a digest the day after `night` shows, from that night's scoreboard."""
    payload = _scoreboard_payload(f"{night:%Y%m%d}") if scoreboard is None else scoreboard
    return build_results(night + timedelta(days=1), _bundle(payload))


def test_regulation_final() -> None:
    first = _results_for(REGULAR_NIGHT)[0]

    assert (first.status, first.overtimes, first.label) == ("final", 0, None)
    assert first.away == ResultSide(
        tricode="LAC",
        score=118,
        leaders=GameLeaders(
            points=Leader(name="James Harden", value=37),
            rebounds=Leader(name="Ivica Zubac", value=12),
            assists=Leader(name="James Harden", value=8),
        ),
    )
    assert (first.home.tricode, first.home.score) == ("BOS", 121)
    assert first.home.leaders is not None
    assert first.home.leaders.points == Leader(name="Jaylen Brown", value=33)


def test_overtime_and_double_overtime() -> None:
    results = _results_for(REGULAR_NIGHT)

    overtimes = {r.home.tricode: r.overtimes for r in results}
    assert overtimes == {"BOS": 0, "SAS": 0, "WAS": 0, "HOU": 1, "UTA": 2}
    assert {r.status for r in results} == {"final"}


def test_results_are_in_tip_off_order() -> None:
    scoreboard = _scoreboard_payload("20251116")
    scoreboard["events"].reverse()

    results = _results_for(REGULAR_NIGHT, scoreboard)

    assert [r.home.tricode for r in results] == ["BOS", "SAS", "WAS", "HOU", "UTA"]


def test_postponed_game_has_no_score_or_leaders() -> None:
    """`scoreboard_20251116_edited.json` is the 2025-11-16 scoreboard trimmed to two games with
    the first hand-edited to postponed: 2025-26 recorded no real postponement."""
    postponed, played = _results_for(REGULAR_NIGHT, _scoreboard_payload("20251116_edited"))

    assert (postponed.status, postponed.overtimes) == ("postponed", 0)
    assert postponed.away == ResultSide(tricode="LAC", score=None, leaders=None)
    assert postponed.home == ResultSide(tricode="BOS", score=None, leaders=None)
    assert (played.status, played.home.score) == ("final", 123)


def test_a_game_that_never_tipped_off_has_no_score() -> None:
    scoreboard = _scoreboard_payload("20251116_edited")
    status = scoreboard["events"][0]["status"]
    status["type"].update(name="STATUS_CANCELED", detail="Canceled")

    result = _results_for(REGULAR_NIGHT, scoreboard)[0]

    assert result.status == "Canceled"
    assert (result.away.score, result.home.score) == (None, None)


def test_other_statuses_render_espn_detail_verbatim() -> None:
    scoreboard = _scoreboard_payload("20251116")
    scoreboard["events"][0]["status"]["type"].update(name="STATUS_SUSPENDED", detail="Suspended")

    result = _results_for(REGULAR_NIGHT, scoreboard)[0]

    assert result.status == "Suspended"
    assert (result.away.score, result.home.score) == (118, 121)


def test_a_missing_leaders_category_is_none() -> None:
    scoreboard = _scoreboard_payload("20251116")
    home = scoreboard["events"][0]["competitions"][0]["competitors"][0]
    home["leaders"] = [c for c in home["leaders"] if c["name"] != "rebounds"]

    leaders = _results_for(REGULAR_NIGHT, scoreboard)[0].home.leaders

    assert leaders is not None
    assert leaders.rebounds is None
    assert leaders.points is not None and leaders.assists is not None


def test_a_side_with_no_leaders_at_all_has_none() -> None:
    scoreboard = _scoreboard_payload("20251116")
    scoreboard["events"][0]["competitions"][0]["competitors"][0]["leaders"] = []

    assert _results_for(REGULAR_NIGHT, scoreboard)[0].home.leaders is None


def test_cup_knockout_is_labelled() -> None:
    results = _results_for(CUP_KNOCKOUT)

    assert [r.label for r in results] == ["NBA Cup - Quarterfinals"] * 2


def test_cup_knockout_label_falls_back_to_nba() -> None:
    scoreboard = _scoreboard_payload(f"{CUP_KNOCKOUT:%Y%m%d}")
    for event in scoreboard["events"]:
        event["competitions"][0]["notes"] = []
    bundle = _bundle(scoreboard, _fixture("nba/schedule_2025-26"))

    results = build_results(CUP_KNOCKOUT + timedelta(days=1), bundle)

    assert [r.label for r in results] == ["Emirates NBA Cup · West Quarterfinal"] * 2


def test_empty_yesterday_builds_no_results() -> None:
    assert _results_for(REGULAR_NIGHT, {"events": []}) == []


def test_unavailable_scoreboard_builds_no_results() -> None:
    assert build_results(REGULAR_NIGHT, SourceBundle()) == []


@pytest.mark.parametrize("night", [PRESEASON, ALL_STAR])
def test_uncounted_games_are_excluded(night: date) -> None:
    assert _results_for(night) == []


def test_games_on_another_day_are_excluded() -> None:
    """A stale snapshot can hold an earlier day's scoreboard."""
    bundle = _bundle(_scoreboard_payload("20251116"))

    assert build_results(date(2025, 11, 18), bundle) == []


def test_game_with_an_unknown_team_is_skipped() -> None:
    scoreboard = _scoreboard_payload("20251116")
    scoreboard["events"][0]["competitions"][0]["competitors"][0]["team"]["id"] = "999"

    results = _results_for(REGULAR_NIGHT, scoreboard)

    assert [r.home.tricode for r in results] == ["SAS", "WAS", "HOU", "UTA"]
