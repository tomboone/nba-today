"""The conference standings (spec §6): final 2025-26 and opening day 2026-27."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nba_today.digest.build import build_standings
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import Clinch, ConferenceStandings, StandingsRow
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.snapshot import Payload

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

FINAL_2025_26 = "standings_2026"
OPENING_2026_27 = "standings_2027"


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / "espn" / f"{name}.json").read_text())


def _standings(payload: Payload) -> list[ConferenceStandings] | None:
    standings = EspnStandings.model_validate(payload)
    return build_standings(SourceBundle(espn_standings=Fetched(standings, FETCHED_AT, stale=False)))


def _final() -> list[ConferenceStandings]:
    standings = _standings(_fixture(FINAL_2025_26))
    assert standings is not None
    return standings


def _row(tricode: str, standings: list[ConferenceStandings] | None = None) -> StandingsRow:
    rows = [row for c in standings or _final() for row in c.rows]
    return next(row for row in rows if row.tricode == tricode)


def _stat(payload: Payload, abbreviation: str, name: str) -> dict[str, object]:
    entries = [e for c in payload["children"] for e in c["standings"]["entries"]]
    entry = next(e for e in entries if e["team"]["abbreviation"] == abbreviation)
    return next(s for s in entry["stats"] if s["name"] == name)


def test_final_standings_are_east_then_west_in_seed_order() -> None:
    standings = _final()

    assert [c.conference for c in standings] == ["East", "West"]
    assert [[row.seed for row in c.rows] for c in standings] == [list(range(1, 16))] * 2
    assert [row.tricode for row in standings[0].rows] == [
        "DET", "BOS", "NYK", "CLE", "TOR", "ATL", "PHI", "ORL",
        "CHA", "MIA", "MIL", "CHI", "BKN", "IND", "WAS",
    ]  # fmt: skip
    assert [row.tricode for row in standings[1].rows] == [
        "OKC", "SAS", "DEN", "LAL", "HOU", "MIN", "POR", "PHX",
        "LAC", "GSW", "NOP", "DAL", "MEM", "SAC", "UTA",
    ]  # fmt: skip


def test_final_standings_row() -> None:
    assert _row("DET") == StandingsRow(
        seed=1,
        tricode="DET",
        wins=60,
        losses=22,
        pct=".732",
        gb="-",
        last_ten="8-2",
        streak="W3",
        clinch=Clinch.BEST_RECORD,
    )
    assert (_row("BOS").gb, _row("WAS").gb, _row("WAS").pct) == ("4", "43", ".207")


def test_a_half_game_behind_keeps_its_half() -> None:
    payload = _fixture(FINAL_2025_26)
    _stat(payload, "BOS", "gamesBehind")["value"] = 4.5

    assert _row("BOS", _standings(payload)).gb == "4.5"


@pytest.mark.parametrize(
    ("tricode", "clinch"),
    [
        ("DET", Clinch.BEST_RECORD),  # z: the conference's best record
        ("OKC", Clinch.BEST_RECORD),  # *: the league's best record
        ("BOS", Clinch.DIVISION),  # y
        ("NYK", Clinch.PLAYOFF),  # x
        ("PHI", Clinch.PLAY_IN),  # xp
        ("MIA", Clinch.PLAY_IN),  # pb
        ("WAS", Clinch.ELIMINATED),  # e
    ],
)
def test_every_clinch_code(tricode: str, clinch: Clinch) -> None:
    assert _row(tricode).clinch is clinch


def test_a_missing_games_behind_is_blank_rather_than_a_dash() -> None:
    payload = _fixture(FINAL_2025_26)
    entries = [e for c in payload["children"] for e in c["standings"]["entries"]]
    boston = next(e for e in entries if e["team"]["abbreviation"] == "BOS")
    boston["stats"] = [s for s in boston["stats"] if s["name"] != "gamesBehind"]

    assert _row("BOS", _standings(payload)).gb == ""


def test_an_unknown_clinch_code_is_none_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    payload = _fixture(FINAL_2025_26)
    _stat(payload, "BOS", "clincher")["displayValue"] = "q"

    with caplog.at_level(logging.WARNING):
        row = _row("BOS", _standings(payload))

    assert row.clinch is None
    assert "unknown clinch code" in caplog.text


def test_opening_day_standings_have_no_clinch_and_every_leader_is_a_dash() -> None:
    standings = _standings(_fixture(OPENING_2026_27))

    assert standings is not None
    east = standings[0].rows
    assert len(east) == 15
    assert [row.tricode for row in east] == sorted(row.tricode for row in east)
    assert east[0] == StandingsRow(
        seed=1,
        tricode="ATL",
        wins=0,
        losses=0,
        pct=".000",
        gb="-",
        last_ten=None,
        streak="-",
        clinch=None,
    )


def test_an_entry_with_an_unknown_team_is_skipped() -> None:
    payload = _fixture(FINAL_2025_26)
    entries = payload["children"][0]["standings"]["entries"]
    next(e for e in entries if e["team"]["abbreviation"] == "DET")["team"]["id"] = "999"

    standings = _standings(payload)

    assert standings is not None
    assert [row.tricode for row in standings[0].rows][:2] == ["BOS", "NYK"]
    assert len(standings[0].rows) == 14


def test_an_entry_with_no_seed_is_skipped() -> None:
    payload = _fixture(FINAL_2025_26)
    entries = payload["children"][1]["standings"]["entries"]
    entry = next(e for e in entries if e["team"]["abbreviation"] == "OKC")
    entry["stats"] = [s for s in entry["stats"] if s["name"] != "playoffSeed"]

    standings = _standings(payload)

    assert standings is not None
    assert "OKC" not in [row.tricode for row in standings[1].rows]


def test_unavailable_standings_build_none() -> None:
    assert build_standings(SourceBundle()) is None
