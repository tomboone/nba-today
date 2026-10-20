"""Whole source bundles built from recorded fixtures, shared by the digest and render tests."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

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
# A first-round night of games 4 and 5, the morning after games 4 (one a 3-1 lead, one tied).
POSTSEASON_DAY = date(2026, 4, 27)
EMPTY_SCOREBOARD: Payload = {"events": []}


def fixture(name: str) -> Payload:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fresh[T](data: T) -> Fetched[T]:
    return Fetched(data, FETCHED_AT, stale=False)


def a_day_later(scoreboard: Payload) -> Payload:
    for event in scoreboard["events"]:
        tip_off = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        event["date"] = (tip_off + timedelta(days=1)).isoformat()
    return scoreboard


def stats[M: BaseModel](model: type[M], name: str) -> list[M]:
    return [model.model_validate(r) for r in parse_result_set(fixture(f"nba/{name}"))]


def bundle(today: Payload, yesterday: Payload, standings: str, season: str) -> SourceBundle:
    return SourceBundle(
        espn_scoreboard_today=fresh(EspnScoreboard.model_validate(today)),
        espn_scoreboard_yesterday=fresh(EspnScoreboard.model_validate(yesterday)),
        espn_standings=fresh(EspnStandings.model_validate(fixture(f"espn/{standings}"))),
        espn_injuries=fresh(EspnInjuries.model_validate(fixture("espn/injuries"))),
        espn_athletes=fresh(EspnAthletes.model_validate(fixture("espn/athletes_2026"))),
        nba_schedule=fresh(NbaSchedule.model_validate(fixture(f"nba/schedule_{season}"))),
        nba_team_advanced=fresh(stats(TeamAdvanced, f"team_advanced_{season}")),
        nba_player_advanced=fresh(stats(PlayerAdvanced, f"player_advanced_{season}")),
        nba_player_base=fresh(stats(PlayerBase, f"player_base_{season}")),
    )


def full_day_bundle() -> SourceBundle:
    yesterday = fixture("espn/scoreboard_20251116")
    today = a_day_later(fixture("espn/scoreboard_20251116"))
    return bundle(today, yesterday, "standings_2026", "2025-26")


def opening_day_bundle() -> SourceBundle:
    """2026-27 opening night: no results yet, and nobody qualifies for the leaderboard."""
    return bundle(
        fixture("espn/scoreboard_20261020"), EMPTY_SCOREBOARD, "standings_2027", "2026-27"
    )


def postseason_bundle() -> SourceBundle:
    """2025-26 first round: tonight's games 4 and 5, including OKC's sweep, and last night's
    games 4."""
    return bundle(
        fixture("espn/scoreboard_20260427"),
        fixture("espn/scoreboard_20260426"),
        "standings_2026",
        "2025-26",
    )
