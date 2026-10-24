"""The whole digest (spec §6): its kind, and which sources were stale or unavailable."""

from dataclasses import fields, replace

from nba_today.digest.build import build_digest
from nba_today.digest.bundle import SourceBundle
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.nba_player_stats import PlayerAdvanced
from tests.bundles import (
    EMPTY_SCOREBOARD,
    FULL_DAY,
    OPENING_DAY,
    SNAPSHOT_AT,
    fresh,
    full_day_bundle,
    opening_day_bundle,
    stats,
)

SNAPSHOT_NAMES = [
    "espn_scoreboard_today",
    "espn_scoreboard_yesterday",
    "espn_standings",
    "espn_injuries",
    "espn_athletes",
    "espn_postseason",
    "nba_schedule",
    "nba_team_advanced",
    "nba_player_advanced",
    "nba_player_base",
]


def test_bundle_fields_are_the_snapshot_names() -> None:
    assert [f.name for f in fields(SourceBundle)] == SNAPSHOT_NAMES


def test_a_full_day() -> None:
    digest = build_digest(FULL_DAY, full_day_bundle())

    assert (digest.date, digest.kind, digest.phase) == (FULL_DAY, "full", "regular")
    assert (digest.stale, digest.unavailable) == ({}, [])
    assert len(digest.matchups) == 5
    assert len(digest.results) == 5
    assert digest.standings is not None
    assert [c.conference for c in digest.standings] == ["East", "West"]
    assert digest.leaderboard is not None
    assert len(digest.leaderboard.rows) == 25


def test_a_scores_only_day() -> None:
    no_games = fresh(EspnScoreboard.model_validate(EMPTY_SCOREBOARD))

    digest = build_digest(FULL_DAY, replace(full_day_bundle(), espn_scoreboard_today=no_games))

    assert digest.kind == "scores_only"
    assert digest.matchups == []
    assert len(digest.results) == 5
    assert digest.standings is not None
    assert digest.leaderboard is not None


def test_opening_day() -> None:
    digest = build_digest(OPENING_DAY, opening_day_bundle())

    assert digest.kind == "full"
    assert [m.home.tricode for m in digest.matchups] == ["DET", "NYK", "SAS"]
    assert digest.results == []
    assert digest.standings is not None
    assert digest.leaderboard is None
    assert digest.unavailable == []


def test_a_stale_source_is_listed_with_its_snapshot_time() -> None:
    stale = Fetched(stats(PlayerAdvanced, "player_advanced_2025-26"), SNAPSHOT_AT, stale=True)

    digest = build_digest(FULL_DAY, replace(full_day_bundle(), nba_player_advanced=stale))

    assert digest.stale == {"nba_player_advanced": SNAPSHOT_AT}
    assert digest.unavailable == []
    assert digest.leaderboard is not None
    assert digest.leaderboard.as_of == SNAPSHOT_AT


def test_an_unavailable_nba_source_drops_the_leaderboard_and_is_listed() -> None:
    digest = build_digest(FULL_DAY, replace(full_day_bundle(), nba_player_base=None))

    assert digest.leaderboard is None
    assert digest.unavailable == ["nba_player_base"]
    assert digest.stale == {}


def test_unavailable_sources_are_listed_in_snapshot_order() -> None:
    """Outside the postseason the postseason source is never fetched, so it is no gap."""
    digest = build_digest(FULL_DAY, SourceBundle())

    assert digest.unavailable == [n for n in SNAPSHOT_NAMES if n != "espn_postseason"]
    assert digest.kind == "scores_only"
    assert (digest.matchups, digest.results) == ([], [])
    assert (digest.standings, digest.leaderboard) == (None, None)
