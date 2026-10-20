"""The canonical team table: one row per franchise, keyed both the ESPN way and the NBA.com way.

The digest displays the NBA tricode everywhere. Rows were taken from ESPN's `/teams` and
`/standings` and NBA.com's `scheduleLeagueV2_1.json` on 2026-10-20 and are hard-coded, since the
set changes about once a decade.
"""

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Literal

type Conference = Literal["East", "West"]


class UnknownTeam(LookupError):
    """No franchise in the table has this key; e.g. a preseason opponent like the London Lions."""


@dataclass(frozen=True, slots=True)
class Team:
    espn_id: int
    espn_abbr: str
    nba_id: int
    tricode: str
    conference: Conference
    short_name: str


TEAMS: tuple[Team, ...] = (
    Team(1, "ATL", 1610612737, "ATL", "East", "Hawks"),
    Team(17, "BKN", 1610612751, "BKN", "East", "Nets"),
    Team(2, "BOS", 1610612738, "BOS", "East", "Celtics"),
    Team(30, "CHA", 1610612766, "CHA", "East", "Hornets"),
    Team(4, "CHI", 1610612741, "CHI", "East", "Bulls"),
    Team(5, "CLE", 1610612739, "CLE", "East", "Cavaliers"),
    Team(6, "DAL", 1610612742, "DAL", "West", "Mavericks"),
    Team(7, "DEN", 1610612743, "DEN", "West", "Nuggets"),
    Team(8, "DET", 1610612765, "DET", "East", "Pistons"),
    Team(9, "GS", 1610612744, "GSW", "West", "Warriors"),
    Team(10, "HOU", 1610612745, "HOU", "West", "Rockets"),
    Team(11, "IND", 1610612754, "IND", "East", "Pacers"),
    Team(12, "LAC", 1610612746, "LAC", "West", "Clippers"),
    Team(13, "LAL", 1610612747, "LAL", "West", "Lakers"),
    Team(29, "MEM", 1610612763, "MEM", "West", "Grizzlies"),
    Team(14, "MIA", 1610612748, "MIA", "East", "Heat"),
    Team(15, "MIL", 1610612749, "MIL", "East", "Bucks"),
    Team(16, "MIN", 1610612750, "MIN", "West", "Timberwolves"),
    Team(3, "NO", 1610612740, "NOP", "West", "Pelicans"),
    Team(18, "NY", 1610612752, "NYK", "East", "Knicks"),
    Team(25, "OKC", 1610612760, "OKC", "West", "Thunder"),
    Team(19, "ORL", 1610612753, "ORL", "East", "Magic"),
    Team(20, "PHI", 1610612755, "PHI", "East", "76ers"),
    Team(21, "PHX", 1610612756, "PHX", "West", "Suns"),
    Team(22, "POR", 1610612757, "POR", "West", "Trail Blazers"),
    Team(23, "SAC", 1610612758, "SAC", "West", "Kings"),
    Team(24, "SA", 1610612759, "SAS", "West", "Spurs"),
    Team(28, "TOR", 1610612761, "TOR", "East", "Raptors"),
    Team(26, "UTAH", 1610612762, "UTA", "West", "Jazz"),
    Team(27, "WSH", 1610612764, "WAS", "East", "Wizards"),
)

_BY_ESPN_ID = {t.espn_id: t for t in TEAMS}
_BY_ESPN_ABBR = {t.espn_abbr: t for t in TEAMS}
_BY_NBA_ID = {t.nba_id: t for t in TEAMS}
_BY_TRICODE = {t.tricode: t for t in TEAMS}


def _lookup[K: Hashable](index: dict[K, Team], key: K, kind: str) -> Team:
    try:
        return index[key]
    except KeyError:
        raise UnknownTeam(f"no team with {kind} {key!r}") from None


def by_espn_id(espn_id: int) -> Team:
    """ESPN sends ids as strings (`"18"`); the source models coerce them to int before this."""
    return _lookup(_BY_ESPN_ID, espn_id, "ESPN id")


def by_espn_abbr(espn_abbr: str) -> Team:
    return _lookup(_BY_ESPN_ABBR, espn_abbr, "ESPN abbreviation")


def by_nba_id(nba_id: int) -> Team:
    return _lookup(_BY_NBA_ID, nba_id, "NBA id")


def by_tricode(tricode: str) -> Team:
    return _lookup(_BY_TRICODE, tricode, "tricode")
