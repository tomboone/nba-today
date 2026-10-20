"""Each matchup side's injury report, team ratings, and key player (spec §6).

Opening night 2026-27 (BOS @ DET, PHI @ NYK, OKC @ SAS) is the base: its teams are the ones in
the recorded athletes fixture. That fixture holds the 2025-26 numbers, so the 2025-26 standings
(82 games a team) stand in as the season they belong to.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from nba_today.digest.build import build_matchups
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import InjuryReport, KeyPlayer, MatchupSide, TeamRatings
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_athletes import EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.models.nba_team_stats import TeamAdvanced
from nba_today.sources.nba import parse_result_set
from nba_today.sources.snapshot import Payload

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

OPENING_NIGHT = date(2026, 10, 20)
CLIPPERS_NIGHT = date(2026, 4, 15)
SPURS = "24"


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _fresh[T](data: T) -> Fetched[T]:
    return Fetched(data, FETCHED_AT, stale=False)


def _bundle(
    day: date = OPENING_NIGHT,
    *,
    injuries: Payload | None = None,
    team_advanced: Payload | None = None,
    athletes: Payload | None = None,
    standings: Payload | None = None,
) -> SourceBundle:
    scoreboard = _fixture(f"espn/scoreboard_{day:%Y%m%d}")
    return SourceBundle(
        espn_scoreboard_today=_fresh(EspnScoreboard.model_validate(scoreboard)),
        espn_injuries=None if injuries is None else _fresh(EspnInjuries.model_validate(injuries)),
        nba_team_advanced=(
            None
            if team_advanced is None
            else _fresh([TeamAdvanced.model_validate(r) for r in parse_result_set(team_advanced)])
        ),
        espn_athletes=None if athletes is None else _fresh(EspnAthletes.model_validate(athletes)),
        espn_standings=(
            None if standings is None else _fresh(EspnStandings.model_validate(standings))
        ),
    )


def _full_bundle(day: date = OPENING_NIGHT) -> SourceBundle:
    return _bundle(
        day,
        injuries=_fixture("espn/injuries"),
        team_advanced=_fixture("nba/team_advanced_2025-26"),
        athletes=_fixture("espn/athletes_2026"),
        standings=_fixture("espn/standings_2026"),
    )


def _sides(bundle: SourceBundle, day: date = OPENING_NIGHT) -> dict[str, MatchupSide]:
    return {side.tricode: side for m in build_matchups(day, bundle) for side in (m.away, m.home)}


def _injury(name: str, status: str, abbreviation: str) -> dict[str, Any]:
    return {
        "status": status,
        "athlete": {"displayName": name, "team": {"abbreviation": abbreviation}},
    }


def _set_stat(athletes: Payload, name: str, category: str, stat: str, value: float) -> None:
    """Overwrite one of an athlete's positional stat values in the raw payload."""
    names = next(c["names"] for c in athletes["categories"] if c["name"] == category)
    row = next(a for a in athletes["athletes"] if a["athlete"]["displayName"] == name)
    values = next(c["values"] for c in row["categories"] if c["name"] == category)
    values[names.index(stat)] = value


def test_sides_carry_injuries_ratings_and_key_player() -> None:
    boston = _sides(_full_bundle())["BOS"]

    assert boston.injuries == InjuryReport(out=["Devin Carter"])
    assert boston.ratings == TeamRatings(off_rtg=120.0, def_rtg=111.7, net_rtg=8.3, pace=95.58)
    assert boston.key_player == KeyPlayer(
        name="Jaylen Brown", ppg=28.704226, rpg=6.9295774, apg=5.1267605
    )


def test_every_opening_night_side_has_its_leading_qualified_scorer() -> None:
    sides = _sides(_full_bundle())

    assert {t: s.key_player and s.key_player.name for t, s in sides.items()} == {
        "BOS": "Jaylen Brown",
        "DET": "Cade Cunningham",
        "PHI": "Tyrese Maxey",
        "NYK": "Jalen Brunson",
        "OKC": "Shai Gilgeous-Alexander",
        "SAS": "Victor Wembanyama",
    }


def test_injury_report_groups_by_status_in_espn_order() -> None:
    injuries = _fixture("espn/injuries")
    boston = next(t for t in injuries["injuries"] if t["displayName"] == "Boston Celtics")
    boston["injuries"] = [
        _injury("Sam Hauser", "Day-To-Day", "BOS"),
        *boston["injuries"],
        _injury("Luka Garza", "Suspension", "BOS"),
        _injury("Neemias Queta", "Out", "BOS"),
        _injury("Derrick White", "Day-To-Day", "BOS"),
    ]

    boston_side = _sides(_bundle(injuries=injuries))["BOS"]

    assert boston_side.injuries == InjuryReport(
        out=["Devin Carter", "Neemias Queta"],
        day_to_day=["Sam Hauser", "Derrick White"],
        other=[("Luka Garza", "Suspension")],
    )


def test_team_with_no_injuries_has_an_empty_report() -> None:
    assert _sides(_full_bundle())["DET"].injuries == InjuryReport()


def test_injury_follows_the_athletes_own_team() -> None:
    """ESPN listed Brandon Ingram, a Raptor, under the Clippers on 2026-10-20."""
    sides = _sides(_bundle(CLIPPERS_NIGHT, injuries=_fixture("espn/injuries")), CLIPPERS_NIGHT)

    assert sides["LAC"].injuries.out == [
        "Bradley Beal",
        "Max Strus",
        "Yanic Konan Niederhauser",
        "Jordan Miller",
    ]


def test_injuries_unavailable_leaves_every_report_empty() -> None:
    bundle = _bundle(athletes=_fixture("espn/athletes_2026"))

    assert all(side.injuries == InjuryReport() for side in _sides(bundle).values())


def test_team_advanced_unavailable_leaves_no_ratings() -> None:
    bundle = _bundle(injuries=_fixture("espn/injuries"))

    assert all(side.ratings is None for side in _sides(bundle).values())


def test_team_missing_from_team_advanced_has_no_ratings() -> None:
    """On opening night, the 2026-27 dashboard has headers and no rows."""
    bundle = _bundle(team_advanced=_fixture("nba/team_advanced_2026-27"))

    assert all(side.ratings is None for side in _sides(bundle).values())


def test_team_with_no_qualified_player_has_no_key_player() -> None:
    athletes = _fixture("espn/athletes_2026")
    for row in athletes["athletes"]:
        if row["athlete"]["teamId"] == SPURS:
            _set_stat(athletes, row["athlete"]["displayName"], "general", "avgMinutes", 19.9)

    sides = _sides(_bundle(athletes=athletes, standings=_fixture("espn/standings_2026")))

    assert sides["SAS"].key_player is None
    assert sides["OKC"].key_player is not None


def test_qualification_needs_seventy_percent_of_team_games() -> None:
    """Wembanyama's 64 games of 82 qualify; 57 do not, which leaves De'Aaron Fox."""
    athletes = _fixture("espn/athletes_2026")
    _set_stat(athletes, "Victor Wembanyama", "general", "gamesPlayed", 57)

    sides = _sides(_bundle(athletes=athletes, standings=_fixture("espn/standings_2026")))

    assert sides["SAS"].key_player is not None
    assert sides["SAS"].key_player.name == "De'Aaron Fox"


def test_opening_day_standings_qualify_nobody() -> None:
    """ESPN answers opening day with last season's athletes, beside 0-0 standings."""
    bundle = _bundle(
        athletes=_fixture("espn/athletes_2026"), standings=_fixture("espn/standings_2027")
    )

    assert all(side.key_player is None for side in _sides(bundle).values())


def test_standings_unavailable_leaves_no_key_player() -> None:
    bundle = _bundle(athletes=_fixture("espn/athletes_2026"))

    assert all(side.key_player is None for side in _sides(bundle).values())


@pytest.mark.parametrize(
    ("fox_rpg", "expected"),
    [(12.0, "De'Aaron Fox"), (11.5, "De'Aaron Fox"), (11.0, "Victor Wembanyama")],
    ids=["rebounds-break-the-tie", "then-name", "rebounds-keep-wembanyama"],
)
def test_key_player_ties_break_on_rebounds_then_name(fox_rpg: float, expected: str) -> None:
    """Fox is given Wembanyama's 25.0 points a game."""
    athletes = _fixture("espn/athletes_2026")
    _set_stat(athletes, "De'Aaron Fox", "offensive", "avgPoints", 25.0)
    _set_stat(athletes, "De'Aaron Fox", "general", "avgRebounds", fox_rpg)

    sides = _sides(_bundle(athletes=athletes, standings=_fixture("espn/standings_2026")))

    assert sides["SAS"].key_player is not None
    assert sides["SAS"].key_player.name == expected
