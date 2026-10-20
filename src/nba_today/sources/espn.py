"""ESPN site API client: the scoreboard, and the counted-game filter (spec §4.2, §5.1).

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

So season type separates preseason (1) from the regular season (2), the postseason (3), and the
play-in (5), but the All-Star games are type 2 and only their note sets them apart. Every
scoreboard's league-level `leagues[].season.type` said 2 regardless, so only the event's own
`season.type` is read. Probing other 2025-26 dates live (not recorded) also showed the
regular-season notes `NBA Cup - Group Play` and `NBA Mexico City Game 2025`; both are counted.

The scoreboard request sends only `dates`, despite §4.2's "pass the season and season type": the
date already fixes the season, and a `seasontype` filter would hide the play-in and playoffs.
"""

from datetime import date, timedelta
from typing import Literal

import httpx

from nba_today.sources.fetched import Fetched, fetch_or_snapshot
from nba_today.sources.models.espn_scoreboard import EspnScoreboard, Event
from nba_today.sources.snapshot import Payload, SnapshotStore

type ScoreboardDay = Literal["today", "yesterday"]

SCOREBOARD_PATH = "/apis/site/v2/sports/basketball/nba/scoreboard"

# Matchups and the plan read today's scoreboard; results read yesterday's (spec §3.2).
SCOREBOARD_SOURCE: dict[ScoreboardDay, str] = {
    "today": "espn_scoreboard_today",
    "yesterday": "espn_scoreboard_yesterday",
}

REGULAR_SEASON = 2
POSTSEASON = 3
PLAY_IN = 5
COUNTED_SEASON_TYPES = frozenset({REGULAR_SEASON, POSTSEASON, PLAY_IN})
ALL_STAR_NOTE_PREFIX = "NBA All-Star"


def get_scoreboard(client: httpx.Client, day: date) -> Payload:
    """The raw scoreboard for one Eastern date; raises on an error status."""
    response = client.get(SCOREBOARD_PATH, params={"dates": day.strftime("%Y%m%d")})
    response.raise_for_status()
    return response.json()


def fetch_scoreboard(
    client: httpx.Client | None, store: SnapshotStore, today: date, day: ScoreboardDay
) -> Fetched[EspnScoreboard]:
    """Today's or yesterday's scoreboard, or its snapshot; with no client, the snapshot alone."""
    requested = today if day == "today" else today - timedelta(days=1)
    fetch = None if client is None else lambda: get_scoreboard(client, requested)
    return fetch_or_snapshot(SCOREBOARD_SOURCE[day], fetch, EspnScoreboard.model_validate, store)


def is_counted(event: Event) -> bool:
    """Whether a game belongs in the digest: regular season, NBA Cup, play-in, or playoffs."""
    if event.season is None or event.season.type not in COUNTED_SEASON_TYPES:
        return False
    return not any(
        note.headline is not None and note.headline.startswith(ALL_STAR_NOTE_PREFIX)
        for competition in event.competitions
        for note in competition.notes
    )
