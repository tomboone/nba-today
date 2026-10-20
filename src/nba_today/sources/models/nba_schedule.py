"""The NBA.com season schedule (`scheduleLeagueV2_1.json`): the fields spec §5.2 reads.

Radio and international broadcasters, leaders, weeks, and the broadcaster list are ignored.
Every field defaults, so one game missing a field never fails the whole schedule. NBA.com sends
an empty string for a missing label or series text; that becomes None.
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


def _parse_game_date(value: object) -> object:
    """`"10/20/2026 00:00:00"`, the Eastern date the games fall on, to a date."""
    if isinstance(value, str):
        return datetime.strptime(value, "%m/%d/%Y %H:%M:%S").date() if value else None
    return value


type LaxStr = Annotated[str | None, BeforeValidator(_blank_to_none)]


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class Broadcaster(_Model):
    broadcaster_display: str | None = None


class Broadcasters(_Model):
    national_tv_broadcasters: list[Broadcaster] = []
    national_ott_broadcasters: list[Broadcaster] = []
    home_tv_broadcasters: list[Broadcaster] = []
    home_ott_broadcasters: list[Broadcaster] = []
    away_tv_broadcasters: list[Broadcaster] = []
    away_ott_broadcasters: list[Broadcaster] = []


class Team(_Model):
    team_id: int | None = None
    team_tricode: str | None = None


class Game(_Model):
    game_id: str | None = None
    # NBA.com capitalises the acronym, which the camelCase generator would not.
    game_date_time_utc: datetime | None = Field(default=None, alias="gameDateTimeUTC")
    home_team: Team | None = None
    away_team: Team | None = None
    game_label: LaxStr = None
    game_sub_label: LaxStr = None
    # "BOS leads 3-1", "Series tied 2-2", "NYK wins 4-1"; on a play-in game, "PHI clinch 7 seed".
    series_text: LaxStr = None
    # "Game 4" on a playoff game; blank otherwise.
    series_game_number: LaxStr = None
    if_necessary: bool | None = None
    broadcasters: Broadcasters | None = None


class GameDate(_Model):
    game_date: Annotated[date | None, BeforeValidator(_parse_game_date)] = None
    games: list[Game] = []


class LeagueSchedule(_Model):
    season_year: str | None = None
    game_dates: list[GameDate] = []


class NbaSchedule(_Model):
    league_schedule: LeagueSchedule | None = None
