"""`leaguedashplayerstats` rows, `MeasureType=Advanced` and `Base`: the PIE leaderboard (§5.2).

A player traded mid-season has one row across every team, under the team they are on now, with
`TEAM_COUNT` above 1: Kevin Huerter (DET) and Nikola Vučević (BOS) in the 2025-26 fixtures.
"""

from pydantic import BaseModel

from nba_today.sources.models.nba_stats_base import STATS_CONFIG, LaxFloat, LaxInt


class PlayerAdvanced(BaseModel):
    model_config = STATS_CONFIG

    player_id: LaxInt = None
    player_name: str | None = None
    team_id: LaxInt = None
    gp: LaxInt = None
    min: LaxFloat = None
    pie: LaxFloat = None
    ts_pct: LaxFloat = None


class PlayerBase(BaseModel):
    model_config = STATS_CONFIG

    player_id: LaxInt = None
    player_name: str | None = None
    team_id: LaxInt = None
    pts: LaxFloat = None
    reb: LaxFloat = None
    ast: LaxFloat = None
