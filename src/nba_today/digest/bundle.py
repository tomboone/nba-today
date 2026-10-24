"""Every fetched source a digest is built from, one field per snapshot name (spec §5)."""

from dataclasses import dataclass

from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_athletes import EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.sources.models.espn_standings import EspnStandings
from nba_today.sources.models.nba_player_stats import PlayerAdvanced, PlayerBase
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.models.nba_team_stats import TeamAdvanced


@dataclass(frozen=True, slots=True)
class SourceBundle:
    """Each source as fetched or loaded from its snapshot; `None` where it had no snapshot either.

    An NBA.com source is also `None` when `NBA_ENRICHMENT` is off, and `espn_postseason` outside
    the postseason phase, when it is not fetched (spec §13). A dataclass rather than a
    pydantic model: it carries already-parsed `Fetched` values, which must not be validated a
    second time.
    """

    espn_scoreboard_today: Fetched[EspnScoreboard] | None = None
    espn_scoreboard_yesterday: Fetched[EspnScoreboard] | None = None
    espn_standings: Fetched[EspnStandings] | None = None
    espn_injuries: Fetched[EspnInjuries] | None = None
    espn_athletes: Fetched[EspnAthletes] | None = None
    espn_postseason: Fetched[EspnScoreboard] | None = None
    nba_schedule: Fetched[NbaSchedule] | None = None
    nba_team_advanced: Fetched[list[TeamAdvanced]] | None = None
    nba_player_advanced: Fetched[list[PlayerAdvanced]] | None = None
    nba_player_base: Fetched[list[PlayerBase]] | None = None
