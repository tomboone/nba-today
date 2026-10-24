"""The postseason (spec §13): the digest's phase, series state on playoff matchups and results,
and the postseason picture in place of the standings.

Fixtures are 2025-26 nights: the last regular-season night (0412), the first and last play-in
nights (0414, 0417), first-round games 2 and 4 (0420, 0426), a sweep (0427), and the Finals
clincher (0613). The picture reads `postseason_2026`, every 2025-26 play-in and playoff game.
"""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from nba_today.digest.build import (
    build_digest,
    build_matchups,
    build_phase,
    build_postseason,
    build_results,
)
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import (
    PlayInGame,
    PlayInSide,
    PostseasonPicture,
    PostseasonTeam,
    SeriesState,
)
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.snapshot import Payload
from tests.bundles import (
    EMPTY_SCOREBOARD,
    FULL_DAY,
    POSTSEASON_DAY,
    fixture,
    fresh,
    full_day_bundle,
    postseason_bundle,
)

LAST_REGULAR_NIGHT = date(2026, 4, 12)
FIRST_PLAY_IN = date(2026, 4, 14)
LAST_PLAY_IN = date(2026, 4, 17)
GAME_TWOS = date(2026, 4, 20)
GAME_FOURS = date(2026, 4, 26)
SWEEP = date(2026, 4, 27)
FINALS_CLINCHER = date(2026, 6, 13)
# The morning after the first round's first games, the morning of conference finals games 5
# (the East swept the night before), and the morning after the Finals.
FIRST_ROUND_OPENS = date(2026, 4, 18)
CONFERENCE_FINALS = date(2026, 5, 26)
AFTER_THE_FINALS = date(2026, 6, 14)


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


def _picture(day: date, standings: Payload | None = None) -> PostseasonPicture:
    """The picture on `day` from the whole 2025-26 postseason and the final standings."""
    bundle = SourceBundle(
        espn_standings=fresh(
            EspnStandings.model_validate(
                fixture("espn/standings_2026") if standings is None else standings
            )
        ),
        espn_postseason=fresh(EspnScoreboard.model_validate(fixture("espn/postseason_2026"))),
    )
    picture = build_postseason(day, bundle)
    assert picture is not None
    return picture


def _rounds(picture: PostseasonPicture) -> dict[str, dict[str, list[tuple[str, str, str | None]]]]:
    """Each conference's rounds, latest first: (better seed, other seed, state) per series."""
    return {
        c.conference: {
            r.label: [
                (f"{s.high.seed} {s.high.tricode}", f"{s.low.seed} {s.low.tricode}", s.summary)
                for s in r.series
            ]
            for r in c.rounds
        }
        for c in picture.conferences
    }


def _side(tricode: str, score: int | None = None) -> PlayInSide:
    return PlayInSide(tricode=tricode, score=score)


def test_mid_play_in_lists_the_play_in_games() -> None:
    """The night's own games are listed unplayed: at send time they have not tipped off."""
    picture = _picture(date(2026, 4, 15))

    east, west = picture.conferences
    assert picture.finals is None
    assert (east.rounds, west.rounds) == ([], [])
    assert [(g.label, g.away, g.home) for g in east.play_in] == [
        ("9th Place vs 10th Place", _side("MIA", 126), _side("CHA", 127)),
        ("7th Place vs 8th Place", _side("ORL"), _side("PHI")),
    ]
    assert [(g.label, g.away, g.home) for g in west.play_in] == [
        ("7th Place vs 8th Place", _side("POR", 114), _side("PHX", 110)),
        ("9th Place vs 10th Place", _side("GSW"), _side("LAC")),
    ]


def test_the_play_in_stays_until_a_playoff_game_is_played() -> None:
    """The first round's opening night lists its series beside the finished play-in."""
    east, _ = _picture(FIRST_ROUND_OPENS).conferences

    assert _rounds(_picture(FIRST_ROUND_OPENS))["East"] == {
        "1st Round": [("3 NYK", "6 ATL", None), ("4 CLE", "5 TOR", None)]
    }
    assert [g.label for g in east.play_in] == [
        "9th Place vs 10th Place",
        "7th Place vs 8th Place",
        "8th Seed Game",
    ]
    assert east.play_in[-1] == PlayInGame(
        label="8th Seed Game",
        tip_off=east.play_in[-1].tip_off,
        away=_side("CHA", 90),
        home=_side("ORL", 121),
    )
    assert [c.play_in for c in _picture(date(2026, 4, 19)).conferences] == [[], []]


def test_the_play_in_goes_once_a_playoff_game_is_played_even_without_espn_series() -> None:
    postseason = fixture("espn/postseason_2026")
    for event in postseason["events"]:
        event["competitions"][0].pop("series", None)
    bundle = SourceBundle(espn_postseason=fresh(EspnScoreboard.model_validate(postseason)))

    picture = build_postseason(GAME_FOURS, bundle)

    assert picture is not None
    assert [c.play_in for c in picture.conferences] == [[], []]
    assert {s.summary for c in picture.conferences for s in c.rounds[0].series} == {None}


def test_mid_first_round_has_four_series_per_conference_each_with_its_state() -> None:
    assert _rounds(_picture(GAME_FOURS)) == {
        "East": {
            "1st Round": [
                ("1 DET", "8 ORL", "ORL leads 2-1"),
                ("2 BOS", "7 PHI", "BOS leads 2-1"),
                ("3 NYK", "6 ATL", "Series tied 2-2"),
                ("4 CLE", "5 TOR", "CLE leads 2-1"),
            ]
        },
        "West": {
            "1st Round": [
                ("1 OKC", "8 PHX", "OKC leads 3-0"),
                ("2 SAS", "7 POR", "SAS leads 2-1"),
                ("3 DEN", "6 MIN", "MIN leads 3-1"),
                ("4 LAL", "5 HOU", "LAL leads 3-0"),
            ]
        },
    }


def test_the_conference_finals_list_every_round_latest_first() -> None:
    rounds = _rounds(_picture(CONFERENCE_FINALS))

    assert list(rounds["East"]) == ["Finals", "Semifinals", "1st Round"]
    assert rounds["East"]["Finals"] == [("3 NYK", "4 CLE", "NYK wins 4-0")]
    assert rounds["West"]["Finals"] == [("1 OKC", "2 SAS", "Series tied 2-2")]
    assert rounds["East"]["Semifinals"] == [
        ("1 DET", "4 CLE", "CLE wins 4-3"),
        ("3 NYK", "7 PHI", "NYK wins 4-0"),
    ]
    assert [len(r) for r in rounds["West"].values()] == [1, 2, 4]


def test_the_finals() -> None:
    picture = _picture(AFTER_THE_FINALS)

    assert picture.finals is not None
    assert (picture.finals.high, picture.finals.low, picture.finals.summary) == (
        PostseasonTeam(tricode="SAS", seed=2),
        PostseasonTeam(tricode="NYK", seed=3),
        "NYK wins 4-1",
    )
    assert _rounds(picture)["West"]["Finals"] == [("1 OKC", "2 SAS", "SAS wins 4-3")]


def test_play_in_teams_take_the_seed_their_first_round_opponent_pairs_with() -> None:
    """Standings that still give the play-in teams their regular-season places: PHX finished
    7th and POR 8th, but POR won the 7th-place game."""
    standings = fixture("espn/standings_2026")
    for conference in standings["children"]:
        for entry in conference["standings"]["entries"]:
            seed = next(s for s in entry["stats"] if s["name"] == "playoffSeed")
            seed["value"] = {"PHX": 7.0, "POR": 8.0}.get(
                entry["team"]["abbreviation"], seed["value"]
            )

    west = _rounds(_picture(GAME_FOURS, standings))["West"]["1st Round"]

    assert [(high, low) for high, low, _ in west[:2]] == [("1 OKC", "8 PHX"), ("2 SAS", "7 POR")]


def test_no_standings_means_no_seeds() -> None:
    bundle = SourceBundle(
        espn_postseason=fresh(EspnScoreboard.model_validate(fixture("espn/postseason_2026")))
    )
    picture = build_postseason(GAME_FOURS, bundle)

    assert picture is not None
    first_round = picture.conferences[0].rounds[0]
    assert len(first_round.series) == 4
    assert {s.high.seed for s in first_round.series} | {s.low.seed for s in first_round.series} == {
        None
    }


def test_before_the_first_play_in_there_is_no_picture() -> None:
    bundle = SourceBundle(
        espn_postseason=fresh(EspnScoreboard.model_validate(fixture("espn/postseason_2026")))
    )

    assert build_postseason(date(2026, 4, 13), bundle) is None
    assert build_postseason(GAME_FOURS, SourceBundle()) is None


def test_a_postseason_digest_shows_the_picture_and_no_standings() -> None:
    digest = build_digest(POSTSEASON_DAY, postseason_bundle())

    assert digest.phase == "postseason"
    assert digest.standings is None
    assert digest.postseason is not None
    assert digest.unavailable == []


def test_an_unavailable_postseason_source_is_listed_in_the_postseason() -> None:
    bundle = replace(postseason_bundle(), espn_postseason=None)

    digest = build_digest(POSTSEASON_DAY, bundle)

    assert (digest.standings, digest.postseason) == (None, None)
    assert digest.unavailable == ["espn_postseason"]


def test_a_regular_digest_has_standings_and_no_picture() -> None:
    bundle = replace(full_day_bundle(), espn_postseason=postseason_bundle().espn_postseason)

    digest = build_digest(FULL_DAY, bundle)

    assert digest.standings is not None
    assert digest.postseason is None
