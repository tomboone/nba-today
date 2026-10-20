"""The whole digest (spec §6): its kind, and which sources were stale or unavailable."""

import json
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from nba_today.digest.build import build_digest
from nba_today.digest.bundle import SourceBundle
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_athletes import EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.models.nba_player_stats import PlayerAdvanced, PlayerBase
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.models.nba_team_stats import TeamAdvanced
from nba_today.sources.nba import parse_result_set
from nba_today.sources.snapshot import Payload

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
SNAPSHOT_AT = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

# The night after the recorded 2025-11-16 slate. No 2025-11-17 scoreboard was recorded, so
# tonight's games are the 11-16 slate moved forward a day.
FULL_DAY = date(2025, 11, 17)
OPENING_DAY = date(2026, 10, 20)
EMPTY_SCOREBOARD: Payload = {"events": []}

SNAPSHOT_NAMES = [
    "espn_scoreboard_today",
    "espn_scoreboard_yesterday",
    "espn_standings",
    "espn_injuries",
    "espn_athletes",
    "nba_schedule",
    "nba_team_advanced",
    "nba_player_advanced",
    "nba_player_base",
]


def _fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _fresh[T](data: T) -> Fetched[T]:
    return Fetched(data, FETCHED_AT, stale=False)


def _a_day_later(scoreboard: Payload) -> Payload:
    for event in scoreboard["events"]:
        tip_off = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        event["date"] = (tip_off + timedelta(days=1)).isoformat()
    return scoreboard


def _stats[M: BaseModel](model: type[M], name: str) -> list[M]:
    return [model.model_validate(r) for r in parse_result_set(_fixture(f"nba/{name}"))]


def _bundle(today: Payload, yesterday: Payload, standings: str, season: str) -> SourceBundle:
    return SourceBundle(
        espn_scoreboard_today=_fresh(EspnScoreboard.model_validate(today)),
        espn_scoreboard_yesterday=_fresh(EspnScoreboard.model_validate(yesterday)),
        espn_standings=_fresh(EspnStandings.model_validate(_fixture(f"espn/{standings}"))),
        espn_injuries=_fresh(EspnInjuries.model_validate(_fixture("espn/injuries"))),
        espn_athletes=_fresh(EspnAthletes.model_validate(_fixture("espn/athletes_2026"))),
        nba_schedule=_fresh(NbaSchedule.model_validate(_fixture(f"nba/schedule_{season}"))),
        nba_team_advanced=_fresh(_stats(TeamAdvanced, f"team_advanced_{season}")),
        nba_player_advanced=_fresh(_stats(PlayerAdvanced, f"player_advanced_{season}")),
        nba_player_base=_fresh(_stats(PlayerBase, f"player_base_{season}")),
    )


def _full_day_bundle() -> SourceBundle:
    yesterday = _fixture("espn/scoreboard_20251116")
    today = _a_day_later(_fixture("espn/scoreboard_20251116"))
    return _bundle(today, yesterday, "standings_2026", "2025-26")


def test_bundle_fields_are_the_snapshot_names() -> None:
    assert [f.name for f in fields(SourceBundle)] == SNAPSHOT_NAMES


def test_a_full_day() -> None:
    digest = build_digest(FULL_DAY, _full_day_bundle())

    assert (digest.date, digest.kind, digest.phase) == (FULL_DAY, "full", "regular")
    assert (digest.stale, digest.unavailable) == ({}, [])
    assert len(digest.matchups) == 5
    assert len(digest.results) == 5
    assert digest.standings is not None
    assert [c.conference for c in digest.standings] == ["East", "West"]
    assert digest.leaderboard is not None
    assert len(digest.leaderboard.rows) == 25


def test_a_scores_only_day() -> None:
    no_games = _fresh(EspnScoreboard.model_validate(EMPTY_SCOREBOARD))

    digest = build_digest(FULL_DAY, replace(_full_day_bundle(), espn_scoreboard_today=no_games))

    assert digest.kind == "scores_only"
    assert digest.matchups == []
    assert len(digest.results) == 5
    assert digest.standings is not None
    assert digest.leaderboard is not None


def test_opening_day() -> None:
    bundle = _bundle(
        _fixture("espn/scoreboard_20261020"), EMPTY_SCOREBOARD, "standings_2027", "2026-27"
    )

    digest = build_digest(OPENING_DAY, bundle)

    assert digest.kind == "full"
    assert [m.home.tricode for m in digest.matchups] == ["DET", "NYK", "SAS"]
    assert digest.results == []
    assert digest.standings is not None
    assert digest.leaderboard is None
    assert digest.unavailable == []


def test_a_stale_source_is_listed_with_its_snapshot_time() -> None:
    stale = Fetched(_stats(PlayerAdvanced, "player_advanced_2025-26"), SNAPSHOT_AT, stale=True)

    digest = build_digest(FULL_DAY, replace(_full_day_bundle(), nba_player_advanced=stale))

    assert digest.stale == {"nba_player_advanced": SNAPSHOT_AT}
    assert digest.unavailable == []
    assert digest.leaderboard is not None
    assert digest.leaderboard.as_of == SNAPSHOT_AT


def test_an_unavailable_nba_source_drops_the_leaderboard_and_is_listed() -> None:
    digest = build_digest(FULL_DAY, replace(_full_day_bundle(), nba_player_base=None))

    assert digest.leaderboard is None
    assert digest.unavailable == ["nba_player_base"]
    assert digest.stale == {}


def test_unavailable_sources_are_listed_in_snapshot_order() -> None:
    digest = build_digest(FULL_DAY, SourceBundle())

    assert digest.unavailable == SNAPSHOT_NAMES
    assert digest.kind == "scores_only"
    assert (digest.matchups, digest.results) == ([], [])
    assert (digest.standings, digest.leaderboard) == (None, None)
