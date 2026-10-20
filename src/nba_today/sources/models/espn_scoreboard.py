"""The ESPN scoreboard response: the fields spec §5.1 reads, everything else ignored.

Every field defaults, so one game missing a field never fails the whole scoreboard; the digest
builder decides what a gap means. ESPN sends numbers such as team
ids and scores as strings, which coerce; a blank one becomes None.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


type LaxInt = Annotated[int | None, BeforeValidator(_blank_to_none)]
type LaxFloat = Annotated[float | None, BeforeValidator(_blank_to_none)]


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class Address(_Model):
    city: str | None = None
    state: str | None = None


class Venue(_Model):
    full_name: str | None = None
    address: Address | None = None


class Note(_Model):
    headline: str | None = None


class SeriesCompetitor(_Model):
    id: LaxInt = None
    wins: LaxInt = None


class Series(_Model):
    type: str | None = None
    summary: str | None = None
    completed: bool | None = None
    competitors: list[SeriesCompetitor] = []


class Broadcast(_Model):
    market: str | None = None
    names: list[str] = []


class Team(_Model):
    id: LaxInt = None
    abbreviation: str | None = None


class Record(_Model):
    type: str | None = None
    summary: str | None = None


class Athlete(_Model):
    display_name: str | None = None


class Leader(_Model):
    value: LaxFloat = None
    athlete: Athlete | None = None


class LeaderCategory(_Model):
    name: str | None = None
    leaders: list[Leader] = []


class Competitor(_Model):
    home_away: str | None = None
    team: Team | None = None
    score: LaxInt = None
    records: list[Record] = []
    leaders: list[LeaderCategory] = []


class Competition(_Model):
    venue: Venue | None = None
    notes: list[Note] = []
    series: Series | None = None
    broadcasts: list[Broadcast] = []
    competitors: list[Competitor] = []


class StatusType(_Model):
    name: str | None = None
    state: str | None = None
    completed: bool | None = None
    detail: str | None = None


class Status(_Model):
    period: LaxInt = None
    type: StatusType | None = None


class EventSeason(_Model):
    year: LaxInt = None
    type: LaxInt = None


class Event(_Model):
    id: str | None = None
    date: datetime | None = None
    season: EventSeason | None = None
    status: Status | None = None
    competitions: list[Competition] = []


class EspnScoreboard(_Model):
    events: list[Event] = []
