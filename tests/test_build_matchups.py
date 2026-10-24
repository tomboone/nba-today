import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nba_today.digest.build import build_matchups, match_nba_game
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import Broadcasts
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.snapshot import Payload

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

REGULAR_NIGHT = date(2025, 11, 16)
CUP_KNOCKOUT = date(2025, 12, 10)
CUP_FINAL = date(2025, 12, 16)
PLAYOFFS = date(2026, 4, 20)
PRESEASON = date(2025, 10, 5)
ALL_STAR = date(2026, 2, 15)


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _scoreboard_payload(day: date) -> Payload:
    return _fixture(f"espn/scoreboard_{day:%Y%m%d}")


def _schedule_payload() -> Payload:
    return _fixture("nba/schedule_2025-26")


def _bundle(
    scoreboard: Payload,
    schedule: Payload | None = None,
    standings: Payload | None = None,
) -> SourceBundle:
    return SourceBundle(
        espn_scoreboard_today=_fresh(EspnScoreboard.model_validate(scoreboard)),
        nba_schedule=None if schedule is None else _fresh(NbaSchedule.model_validate(schedule)),
        espn_standings=(
            None if standings is None else _fresh(EspnStandings.model_validate(standings))
        ),
    )


def _fresh[T](data: T) -> Fetched[T]:
    return Fetched(data, FETCHED_AT, stale=False)


def _full_bundle(day: date) -> SourceBundle:
    return _bundle(_scoreboard_payload(day), _schedule_payload(), _fixture("espn/standings_2026"))


def test_regular_night_merges_nba_local_tv_into_home_and_away() -> None:
    matchups = build_matchups(REGULAR_NIGHT, _full_bundle(REGULAR_NIGHT))

    assert [(m.away.tricode, m.home.tricode) for m in matchups] == [
        ("LAC", "BOS"),
        ("SAC", "SAS"),
        ("BKN", "WAS"),
        ("ORL", "HOU"),
        ("CHI", "UTA"),
    ]
    first = matchups[0]
    assert first.tip_off == datetime(2025, 11, 16, 20, 30, tzinfo=UTC)
    assert first.tv == Broadcasts(
        home=["NBC SPORTS BOSTON"], away=["FanDuel Sports Network - SoCal/KTLA"]
    )
    assert (first.arena, first.city, first.label) == ("TD Garden", "Boston", None)
    assert (first.home.wins, first.home.losses) == (7, 7)
    assert (first.away.wins, first.away.losses) == (4, 9)
    assert (first.home.last_ten, first.home.streak) == ("8-2", "W2")


def test_nba_tv_and_streaming_are_merged_in_source_order() -> None:
    matchups = build_matchups(PLAYOFFS, _full_bundle(PLAYOFFS))

    tv = {m.home.tricode: m.tv for m in matchups}
    assert tv["CLE"].national == ["NBC Sports Network", "Peacock"]
    assert tv["CLE"].away == ["TSN"]


def test_nba_broadcasts_are_deduped() -> None:
    schedule = _schedule_payload()
    game = schedule["leagueSchedule"]["gameDates"][0]["games"][0]
    home = game["broadcasters"]["homeTvBroadcasters"][0]["broadcasterDisplay"]
    game["broadcasters"]["homeOttBroadcasters"] = [{"broadcasterDisplay": home}, {}]

    matchups = build_matchups(REGULAR_NIGHT, _bundle(_scoreboard_payload(REGULAR_NIGHT), schedule))

    assert matchups[0].tv.home == [home]


def test_nba_schedule_unavailable_falls_back_to_espn_without_league_pass() -> None:
    bundle = _bundle(_scoreboard_payload(REGULAR_NIGHT))

    matchups = build_matchups(REGULAR_NIGHT, bundle)

    assert matchups[0].tv == Broadcasts(home=["NBC Sports BO"], away=["FanDuel SN SoCal"])
    assert all("NBA League Pass" not in m.tv.home + m.tv.away + m.tv.national for m in matchups)


def test_game_with_no_nba_match_falls_back_to_espn() -> None:
    schedule = _schedule_payload()
    for game_date in schedule["leagueSchedule"]["gameDates"]:
        game_date["games"] = [
            g for g in game_date["games"] if g["homeTeam"]["teamTricode"] != "BOS"
        ]
    bundle = _bundle(_scoreboard_payload(REGULAR_NIGHT), schedule)

    matchups = build_matchups(REGULAR_NIGHT, bundle)

    tv = {m.home.tricode: m.tv for m in matchups}
    assert tv["BOS"] == Broadcasts(home=["NBC Sports BO"], away=["FanDuel SN SoCal"])
    assert tv["SAS"] == Broadcasts(home=["FanDuel Sports Network - Southwest"], away=["NBCSCA"])


def test_match_nba_game_keys_on_eastern_date_and_tricodes() -> None:
    scoreboard = EspnScoreboard.model_validate(_scoreboard_payload(REGULAR_NIGHT))
    schedule = NbaSchedule.model_validate(_schedule_payload())
    # 00:00 UTC on the 17th is 19:00 Eastern on the 16th, the NBA.com game date.
    late = next(e for e in scoreboard.events if e.date == datetime(2025, 11, 17, tzinfo=UTC))

    game = match_nba_game(late, schedule)

    assert game is not None
    assert game.home_team is not None and game.home_team.team_tricode == "HOU"
    assert game.away_team is not None and game.away_team.team_tricode == "ORL"


def test_cup_knockout_is_labelled_from_espn() -> None:
    matchups = build_matchups(CUP_KNOCKOUT, _full_bundle(CUP_KNOCKOUT))

    assert [m.label for m in matchups] == ["NBA Cup - Quarterfinals"] * 2


def test_cup_knockout_label_falls_back_to_nba() -> None:
    scoreboard = _scoreboard_payload(CUP_KNOCKOUT)
    for event in scoreboard["events"]:
        event["competitions"][0]["notes"] = []

    matchups = build_matchups(CUP_KNOCKOUT, _bundle(scoreboard, _schedule_payload()))

    assert [m.label for m in matchups] == ["Emirates NBA Cup · West Quarterfinal"] * 2


@pytest.mark.parametrize("headline", ["NBA Cup - Group Play", "NBA Mexico City Game 2025"])
def test_other_regular_season_notes_carry_no_label(headline: str) -> None:
    scoreboard = _scoreboard_payload(REGULAR_NIGHT)
    scoreboard["events"][0]["competitions"][0]["notes"] = [{"headline": headline}]

    matchups = build_matchups(REGULAR_NIGHT, _bundle(scoreboard))

    assert matchups[0].label is None


def test_postseason_games_are_labelled() -> None:
    matchups = build_matchups(PLAYOFFS, _full_bundle(PLAYOFFS))

    assert {m.label for m in matchups} == {"East 1st Round - Game 2", "West 1st Round - Game 2"}


def test_cup_final_takes_records_from_the_standings() -> None:
    final = build_matchups(CUP_FINAL, _full_bundle(CUP_FINAL))[0]
    without_standings = build_matchups(
        CUP_FINAL, _bundle(_scoreboard_payload(CUP_FINAL), _schedule_payload())
    )[0]

    assert (final.home.tricode, final.home.wins, final.home.losses) == ("NYK", 53, 29)
    assert (without_standings.home.wins, without_standings.home.last_ten) == (None, None)


@pytest.mark.parametrize("day", [PRESEASON, ALL_STAR])
def test_uncounted_games_are_excluded(day: date) -> None:
    assert build_matchups(day, _bundle(_scoreboard_payload(day))) == []


def test_games_on_another_day_are_excluded() -> None:
    """A stale snapshot can hold an earlier day's scoreboard."""
    bundle = _bundle(_scoreboard_payload(REGULAR_NIGHT))

    assert build_matchups(date(2025, 11, 17), bundle) == []


def test_unavailable_scoreboard_builds_no_matchups() -> None:
    assert build_matchups(REGULAR_NIGHT, SourceBundle()) == []


def test_game_with_an_unknown_team_is_skipped() -> None:
    scoreboard = _scoreboard_payload(REGULAR_NIGHT)
    scoreboard["events"][0]["competitions"][0]["competitors"][0]["team"]["id"] = "999"

    matchups = build_matchups(REGULAR_NIGHT, _bundle(scoreboard))

    assert len(matchups) == 4
    assert "BOS" not in {m.home.tricode for m in matchups}


def test_same_time_tip_offs_are_ordered_by_home_tricode() -> None:
    scoreboard = _scoreboard_payload(REGULAR_NIGHT)
    # BKN @ WAS comes before ORL @ HOU in ESPN's order; give them the same tip-off.
    events = {e["competitions"][0]["venue"]["address"]["city"]: e for e in scoreboard["events"]}
    events["Washington"]["date"] = events["Houston"]["date"]

    matchups = build_matchups(REGULAR_NIGHT, _bundle(scoreboard))

    assert [m.home.tricode for m in matchups] == ["BOS", "SAS", "HOU", "WAS", "UTA"]


def test_digest_imports_no_networking_modules() -> None:
    """Checked in a fresh interpreter, so transitive imports count too."""
    networking = {
        "socket",
        "ssl",
        "http.client",
        "urllib.request",
        "requests",
        "httpx",
        "curl_cffi",
        "resend",
        "smtplib",
    }
    code = (
        "import sys\n"
        "import nba_today.digest.build, nba_today.digest.bundle, nba_today.digest.models\n"
        f"print(sorted({networking!r} & set(sys.modules)))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"
