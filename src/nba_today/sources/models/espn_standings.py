"""The ESPN standings response: each team's line, its `stats[]` flattened by name (spec §5.1).

ESPN sends each entry's stats as a list of `{name, value, displayValue, ...}`. The entry model
looks them up by `name`: counts and rates come from `value`, and the three stats whose meaning is
only in the text — `streak` (`W3`, `L10`), `clincher` (`z`, `xp`, ...), and `Last Ten Games`
(`8-2`) — come from `displayValue`. The other stats ESPN sends are ignored.

Entries arrive in no useful order; the digest builder sorts by `playoff_seed`. Before a season's
first game, as in the 2026-27 standings recorded on opening day, every team is 0-0 at seed 1 with
streak `-`, and there is no `clincher` or `Last Ten Games` at all.
"""

from typing import Any, cast

from pydantic import BaseModel, Field, model_validator

from nba_today.sources.models.espn_base import ESPN_CONFIG, LaxFloat, LaxInt, Team, dicts

LAST_TEN_GAMES = "Last Ten Games"
DISPLAY_STATS = frozenset({"streak", "clincher", LAST_TEN_GAMES})


class _Model(BaseModel):
    model_config = ESPN_CONFIG


class Entry(_Model):
    team: Team | None = None
    wins: LaxInt = None
    losses: LaxInt = None
    win_percent: LaxFloat = None
    games_behind: LaxFloat = None
    playoff_seed: LaxInt = None
    streak: str | None = None
    clincher: str | None = None
    last_ten_games: str | None = Field(default=None, alias=LAST_TEN_GAMES)

    @model_validator(mode="before")
    @classmethod
    def _flatten_stats(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        entry = cast(dict[str, Any], data)
        flat: dict[str, Any] = {"team": entry.get("team")}
        for stat in dicts(entry.get("stats")):
            name = stat.get("name")
            if isinstance(name, str):
                flat[name] = stat.get("displayValue" if name in DISPLAY_STATS else "value")
        return flat


class Standings(_Model):
    entries: list[Entry] = []


class Conference(_Model):
    name: str | None = None
    abbreviation: str | None = None
    standings: Standings | None = None


class EspnStandings(_Model):
    children: list[Conference] = []
