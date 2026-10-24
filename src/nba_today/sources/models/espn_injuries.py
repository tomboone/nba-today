"""The ESPN injuries response: the league's injury report, grouped by team (spec §5.1).

Each group names its team by `id` and `displayName` ("Atlanta Hawks") only, with no
abbreviation, so a report is matched to a side through the injured athlete's own
`athlete.team.{id, abbreviation}`. The two can disagree: on 2026-10-20 Brandon Ingram was listed
under the LA Clippers (team 12) with `athlete.team` Toronto (28), kept in the recorded fixture.

`status` is the display text. Recorded on 2026-10-20: `Out` (36) and `Day-To-Day` (54).
"""

from pydantic import BaseModel

from nba_today.sources.models.espn_base import ESPN_CONFIG, LaxInt, Team


class _Model(BaseModel):
    model_config = ESPN_CONFIG


class Athlete(_Model):
    display_name: str | None = None
    team: Team | None = None


class Injury(_Model):
    status: str | None = None
    athlete: Athlete | None = None


class TeamInjuries(_Model):
    id: LaxInt = None
    display_name: str | None = None
    injuries: list[Injury] = []


class EspnInjuries(_Model):
    injuries: list[TeamInjuries] = []
