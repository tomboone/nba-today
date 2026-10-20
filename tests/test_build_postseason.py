"""The postseason (spec §13): the digest's phase, and series state on playoff matchups and results.

Fixtures are 2025-26 nights: the last regular-season night (0412), the first and last play-in
nights (0414, 0417), first-round games 2 and 4 (0420, 0426), a sweep (0427), and the Finals
clincher (0613).
"""

from datetime import date, timedelta

import pytest

from nba_today.digest.build import build_digest, build_matchups, build_phase, build_results
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import SeriesState
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.snapshot import Payload
from tests.bundles import EMPTY_SCOREBOARD, FULL_DAY, fixture, fresh, full_day_bundle

LAST_REGULAR_NIGHT = date(2026, 4, 12)
FIRST_PLAY_IN = date(2026, 4, 14)
LAST_PLAY_IN = date(2026, 4, 17)
GAME_TWOS = date(2026, 4, 20)
GAME_FOURS = date(2026, 4, 26)
SWEEP = date(2026, 4, 27)
FINALS_CLINCHER = date(2026, 6, 13)


def _scoreboard(night: date) -> Payload:
    return fixture(f"espn/scoreboard_{night:%Y%m%d}")


def _bundle(
    today: Payload = EMPTY_SCOREBOARD,
    yesterday: Payload = EMPTY_SCOREBOARD,
    schedule: Payload | None = None,
) -> SourceBundle:
    return SourceBundle(
        espn_scoreboard_today=fresh(EspnScoreboard.model_validate(today)),
        espn_scoreboard_yesterday=fresh(EspnScoreboard.model_validate(yesterday)),
        nba_schedule=None if schedule is None else fresh(NbaSchedule.model_validate(schedule)),
    )


def _schedule() -> Payload:
    return fixture("nba/schedule_2025-26")


def _series_today(night: date, scoreboard: Payload | None = None) -> dict[str, SeriesState | None]:
    """Each matchup's series state on `night`, keyed by home tricode."""
    payload = _scoreboard(night) if scoreboard is None else scoreboard
    matchups = build_matchups(night, _bundle(today=payload, schedule=_schedule()))
    return {m.home.tricode: m.series for m in matchups}


def _series_after(night: date) -> dict[str, SeriesState | None]:
    """Each of `night`'s results' series state, as the next day's digest shows it."""
    bundle = _bundle(yesterday=_scoreboard(night), schedule=_schedule())
    return {r.home.tricode: r.series for r in build_results(night + timedelta(days=1), bundle)}


def test_play_in_games_are_labelled_and_have_no_series() -> None:
    first = build_matchups(FIRST_PLAY_IN, _bundle(today=_scoreboard(FIRST_PLAY_IN)))
    last = build_matchups(LAST_PLAY_IN, _bundle(today=_scoreboard(LAST_PLAY_IN)))

    assert [m.label for m in first + last] == [
        "NBA Play-In - East - 9th Place vs 10th Place",
        "NBA Play-In - West - 7th Place vs 8th Place",
        "NBA Play-In - East - 8th Seed Game",
        "NBA Play-In - West - 8th Seed Game",
    ]
    assert [m.series for m in first + last] == [None] * 4


def test_a_tied_series_and_a_lead() -> None:
    series = _series_today(GAME_TWOS)

    assert series["NYK"] == SeriesState(
        round_label="East 1st Round", game_number=2, summary="Series tied 1-1"
    )
    assert series["CLE"] == SeriesState(
        round_label="East 1st Round", game_number=2, summary="CLE leads 2-0"
    )


def test_three_one_leads_are_summarised_in_tricodes() -> None:
    """ESPN's own summary says `SA leads series 3-1`; the digest says `SAS`, as NBA.com does."""
    series = _series_today(GAME_FOURS)

    assert series["TOR"] == SeriesState(
        round_label="East 1st Round", game_number=4, summary="Series tied 2-2"
    )
    assert series["POR"] == SeriesState(
        round_label="West 1st Round", game_number=4, summary="SAS leads 3-1"
    )
    assert series["PHI"] == SeriesState(
        round_label="East 1st Round", game_number=4, summary="BOS leads 3-1"
    )


def test_a_clinching_result() -> None:
    series = _series_after(SWEEP)

    assert series["PHX"] == SeriesState(
        round_label="West 1st Round", game_number=4, summary="OKC wins 4-0"
    )
    assert series["DEN"] == SeriesState(
        round_label="West 1st Round", game_number=5, summary="MIN leads 3-2"
    )


def test_a_finals_result() -> None:
    assert _series_after(FINALS_CLINCHER) == {
        "SAS": SeriesState(round_label="NBA Finals", game_number=5, summary="NYK wins 4-1")
    }


def test_a_regular_season_game_has_no_series_though_espn_sends_one() -> None:
    """ESPN hangs OKC and PHX's later first-round sweep on their last regular-season game."""
    series = _series_today(LAST_REGULAR_NIGHT)

    assert "OKC" in series
    assert list(series.values()) == [None] * len(series)


def test_each_part_falls_back_to_the_nba_schedule() -> None:
    scoreboard = _scoreboard(GAME_FOURS)
    for event in scoreboard["events"]:
        competition = event["competitions"][0]
        competition["notes"] = []
        del competition["series"]

    series = _series_today(GAME_FOURS, scoreboard)

    assert series["POR"] == SeriesState(
        round_label="West First Round", game_number=4, summary="SAS leads 3-1"
    )


def test_no_series_from_either_source_is_none() -> None:
    scoreboard = _scoreboard(GAME_FOURS)
    for event in scoreboard["events"]:
        competition = event["competitions"][0]
        competition["notes"] = []
        del competition["series"]

    matchups = build_matchups(GAME_FOURS, _bundle(today=scoreboard))

    assert [m.series for m in matchups] == [None] * 3


def test_a_series_before_game_one_has_no_summary() -> None:
    scoreboard = _scoreboard(GAME_TWOS)
    for event in scoreboard["events"]:
        competition = event["competitions"][0]
        competition["notes"] = [{"headline": "East 1st Round - Game 1"}]
        for competitor in competition["series"]["competitors"]:
            competitor["wins"] = 0

    matchups = build_matchups(GAME_TWOS, _bundle(today=scoreboard))

    assert [m.series for m in matchups] == [
        SeriesState(round_label="East 1st Round", game_number=1, summary=None)
    ] * 3


@pytest.mark.parametrize(
    ("day", "today", "yesterday"),
    [
        (FIRST_PLAY_IN, FIRST_PLAY_IN, None),
        (date(2026, 4, 18), None, LAST_PLAY_IN),
        (GAME_FOURS, GAME_FOURS, None),
        (date(2026, 4, 28), None, SWEEP),
        (date(2026, 6, 14), None, FINALS_CLINCHER),
    ],
    ids=["play-in", "after the play-in", "game fours", "after a sweep", "after the Finals"],
)
def test_postseason_days(day: date, today: date | None, yesterday: date | None) -> None:
    bundle = _bundle(
        today=EMPTY_SCOREBOARD if today is None else _scoreboard(today),
        yesterday=EMPTY_SCOREBOARD if yesterday is None else _scoreboard(yesterday),
    )

    assert build_digest(day, bundle).phase == "postseason"


def test_a_regular_day_is_regular() -> None:
    assert build_digest(FULL_DAY, full_day_bundle()).phase == "regular"


def test_the_day_after_the_regular_season_ends_is_regular() -> None:
    bundle = _bundle(yesterday=_scoreboard(LAST_REGULAR_NIGHT))

    assert build_phase(date(2026, 4, 13), bundle) == "regular"


def test_a_stale_postseason_snapshot_does_not_make_the_phase() -> None:
    """A snapshot of an earlier day's scoreboard holds none of today's or yesterday's games."""
    bundle = _bundle(today=_scoreboard(GAME_FOURS), yesterday=_scoreboard(GAME_FOURS))

    assert build_phase(date(2026, 5, 2), bundle) == "regular"


def test_no_scoreboards_is_regular() -> None:
    assert build_phase(GAME_FOURS, SourceBundle()) == "regular"
