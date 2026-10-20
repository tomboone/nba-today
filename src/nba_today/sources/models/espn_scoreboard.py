"""The ESPN scoreboard response: the fields spec §5.1 reads, everything else ignored.

Every field defaults, so one game missing a field never fails the whole scoreboard; the digest
builder decides what a gap means.

`is_counted` is the only counted-game filter. What ESPN sent for each kind of game, as recorded
in `tests/fixtures/espn/` on 2026-10-20:

| Fixture  | Games                   | `season.type` | `notes[0].headline`                  |
|----------|-------------------------|---------------|--------------------------------------|
| 20251005 | preseason               | 1             | (none)                               |
| 20251116 | regular season, OT, 2OT | 2             | (none)                               |
| 20251210 | NBA Cup quarterfinals   | 2             | `NBA Cup - Quarterfinals`            |
| 20251216 | NBA Cup final           | 2             | `NBA Cup Championship`               |
| 20260215 | All-Star                | 2             | `NBA All-Star - Championship`, ...   |
| 20260415 | play-in                 | 5             | `NBA Play-In - East - 7th Place ...` |
| 20260420 | playoffs                | 3             | `East 1st Round - Game 2`            |
| 20261020 | 2026-27 opening night   | 2             | (none)                               |

2025-26 had no postponement to record, so `20251116_edited` is the 20251116 scoreboard trimmed
to two games, with the first hand-edited to `STATUS_POSTPONED` (period 0, scores `"0"`, no
leaders).

So season type separates preseason (1) from the regular season (2), the postseason (3), and the
play-in (5), but the All-Star games are type 2 and only their note sets them apart. Every
scoreboard's league-level `leagues[].season.type` said 2 regardless, so only the event's own
`season.type` is read. Probing other 2025-26 dates live (not recorded) also showed the
regular-season notes `NBA Cup - Group Play` and `NBA Mexico City Game 2025`; both are counted.
"""

from datetime import datetime

from pydantic import BaseModel

from nba_today.sources.models.espn_base import ESPN_CONFIG, LaxFloat, LaxInt, Team


class _Model(BaseModel):
    model_config = ESPN_CONFIG


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


REGULAR_SEASON = 2
POSTSEASON = 3
PLAY_IN = 5
COUNTED_SEASON_TYPES = frozenset({REGULAR_SEASON, POSTSEASON, PLAY_IN})
ALL_STAR_NOTE_PREFIX = "NBA All-Star"


def is_counted(event: Event) -> bool:
    """Whether a game belongs in the digest: regular season, NBA Cup, play-in, or playoffs."""
    if event.season is None or event.season.type not in COUNTED_SEASON_TYPES:
        return False
    return not any(
        note.headline is not None and note.headline.startswith(ALL_STAR_NOTE_PREFIX)
        for competition in event.competitions
        for note in competition.notes
    )
