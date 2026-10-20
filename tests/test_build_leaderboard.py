"""The PIE leaderboard (spec §6), from the recorded 2025-26 and opening-day 2026-27 stats.

Every team in the 2025-26 fixture played 82 games, so a player qualifies with at least 58
(`ceil(0.70 * 82)`) and 20 minutes a game: Isaiah Stewart (58) does, Ajay Mitchell (57) does not.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nba_today.digest.build import build_leaderboard
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import Leaderboard, LeaderRow
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.nba_player_stats import PlayerAdvanced, PlayerBase
from nba_today.sources.models.nba_team_stats import TeamAdvanced
from nba_today.sources.nba import parse_result_set

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ADVANCED_AT = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)


def _rows(name: str) -> list[dict[str, Any]]:
    return parse_result_set(json.loads((FIXTURES / "nba" / f"{name}.json").read_text()))


def _bundle(
    season: str = "2025-26",
    *,
    advanced: list[dict[str, Any]] | None = None,
    base: list[dict[str, Any]] | None = None,
    team: list[dict[str, Any]] | None = None,
) -> SourceBundle:
    advanced = _rows(f"player_advanced_{season}") if advanced is None else advanced
    base = _rows(f"player_base_{season}") if base is None else base
    team = _rows(f"team_advanced_{season}") if team is None else team
    return SourceBundle(
        nba_player_advanced=Fetched(
            [PlayerAdvanced.model_validate(r) for r in advanced], ADVANCED_AT, stale=False
        ),
        nba_player_base=Fetched(
            [PlayerBase.model_validate(r) for r in base], FETCHED_AT, stale=False
        ),
        nba_team_advanced=Fetched(
            [TeamAdvanced.model_validate(r) for r in team], FETCHED_AT, stale=False
        ),
    )


def _leaderboard(bundle: SourceBundle | None = None) -> Leaderboard:
    leaderboard = build_leaderboard(_bundle() if bundle is None else bundle)
    assert leaderboard is not None
    return leaderboard


def _names(leaderboard: Leaderboard) -> list[str]:
    return [row.name for row in leaderboard.rows]


def test_top_25_by_pie() -> None:
    leaderboard = _leaderboard()

    assert [row.rank for row in leaderboard.rows] == list(range(1, 26))
    # Towns and Holmgren tie on PIE (.154); Towns scores more.
    assert _names(leaderboard)[:6] == [
        "Victor Wembanyama",
        "Shai Gilgeous-Alexander",
        "Jalen Duren",
        "Cade Cunningham",
        "Karl-Anthony Towns",
        "Chet Holmgren",
    ]
    pies = [row.pie for row in leaderboard.rows]
    assert pies == sorted(pies, reverse=True)
    assert leaderboard.rows[0] == LeaderRow(
        rank=1,
        name="Victor Wembanyama",
        tricode="SAS",
        pts=25.0,
        reb=11.5,
        ast=3.1,
        ts_pct=0.626,
        pie=0.209,
    )


def test_as_of_is_the_advanced_snapshot_time() -> None:
    assert _leaderboard().as_of == ADVANCED_AT


def test_qualification_boundary() -> None:
    """33 of the 42 recorded players qualify; the 70% line falls between 57 and 58 games."""
    names = _names(_leaderboard(_bundle(advanced=_with_pie(_rows("player_advanced_2025-26")))))

    assert "Isaiah Stewart" in names
    assert "Ajay Mitchell" not in names


def _with_pie(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lift the two players either side of the line to the top, so the top 25 can show both."""
    for row in rows:
        if row["PLAYER_NAME"] in ("Isaiah Stewart", "Ajay Mitchell"):
            row["PIE"] = 0.5
    return rows


def test_a_player_under_20_minutes_does_not_qualify() -> None:
    rows = _with_pie(_rows("player_advanced_2025-26"))
    stewart = next(r for r in rows if r["PLAYER_NAME"] == "Isaiah Stewart")
    stewart["MIN"] = 19.9

    assert "Isaiah Stewart" not in _names(_leaderboard(_bundle(advanced=rows)))


def test_pie_ties_break_on_points() -> None:
    rows = _rows("player_advanced_2025-26")
    for row in rows:
        if row["PLAYER_NAME"] in ("Jalen Brunson", "Tyrese Maxey"):
            row["PIE"] = 0.3

    # Maxey scores 28.3 a game to Brunson's 26.0.
    assert _names(_leaderboard(_bundle(advanced=rows)))[:2] == ["Tyrese Maxey", "Jalen Brunson"]


def test_a_player_missing_from_the_base_stats_is_left_out() -> None:
    base = [r for r in _rows("player_base_2025-26") if r["PLAYER_NAME"] != "Victor Wembanyama"]

    assert "Victor Wembanyama" not in _names(_leaderboard(_bundle(base=base)))


def test_no_qualified_players_builds_none() -> None:
    """Opening day: no games played, no rows."""
    assert build_leaderboard(_bundle("2026-27")) is None


def test_rows_with_no_qualified_player_build_none() -> None:
    """Every team at 1,000 games puts every player short of 70%."""
    team = _rows("team_advanced_2025-26")
    for row in team:
        row["GP"] = 1000

    assert build_leaderboard(_bundle(team=team)) is None


def test_any_nba_source_unavailable_builds_none() -> None:
    bundle = _bundle()

    assert build_leaderboard(replace(bundle, nba_player_advanced=None)) is None
    assert build_leaderboard(replace(bundle, nba_player_base=None)) is None
    assert build_leaderboard(replace(bundle, nba_team_advanced=None)) is None
