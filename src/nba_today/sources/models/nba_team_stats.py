"""A `leaguedashteamstats` row, `MeasureType=Advanced`: team ratings (spec §5.2)."""

from pydantic import BaseModel

from nba_today.sources.models.nba_stats_base import STATS_CONFIG, LaxFloat, LaxInt


class TeamAdvanced(BaseModel):
    model_config = STATS_CONFIG

    team_id: LaxInt = None
    gp: LaxInt = None
    off_rating: LaxFloat = None
    def_rating: LaxFloat = None
    net_rating: LaxFloat = None
    pace: LaxFloat = None
