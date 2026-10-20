"""The ESPN per-athlete statistics response, for key players (spec §5.1).

ESPN sends each athlete's stats as positional `values` per category, and names the positions once
at the top level: `categories[].{name, names}`. The root model zips the two, so each athlete gets
one sub-model per category with named fields. Pinned against the 2025-26 fixture:

| Stat | Category    | Name          |
|------|-------------|---------------|
| GP   | `general`   | `gamesPlayed` |
| MPG  | `general`   | `avgMinutes`  |
| RPG  | `general`   | `avgRebounds` |
| PPG  | `offensive` | `avgPoints`   |
| APG  | `offensive` | `avgAssists`  |

The athlete's team is `athlete.teamId` and `athlete.teamShortName` (an ESPN abbreviation such as
`GS`): the team they are on now. A player traded mid-season has one line of stats across every
team, which `athlete.teams` lists alphabetically by abbreviation, so only `teamId` says which is
current.

`requestedSeason.year` is the season ESPN actually answered for, which is not always the one
asked for: on 2026-10-20, opening day, `season=2027` returned the 2025-26 numbers with
`requestedSeason.year` 2026.
"""

from typing import Any, cast

from pydantic import BaseModel, model_validator

from nba_today.sources.models.espn_base import ESPN_CONFIG, LaxFloat, LaxInt, dicts


class _Model(BaseModel):
    model_config = ESPN_CONFIG


class AthleteInfo(_Model):
    id: str | None = None
    display_name: str | None = None
    team_id: LaxInt = None
    team_short_name: str | None = None


class GeneralStats(_Model):
    games_played: LaxInt = None
    avg_minutes: LaxFloat = None
    avg_rebounds: LaxFloat = None


class OffensiveStats(_Model):
    avg_points: LaxFloat = None
    avg_assists: LaxFloat = None


class AthleteStats(_Model):
    athlete: AthleteInfo | None = None
    general: GeneralStats | None = None
    offensive: OffensiveStats | None = None


class RequestedSeason(_Model):
    year: LaxInt = None


class EspnAthletes(_Model):
    requested_season: RequestedSeason | None = None
    athletes: list[AthleteStats] = []

    @model_validator(mode="before")
    @classmethod
    def _name_category_values(cls, data: object) -> object:
        """Copy each athlete with `{category: {name: value}}`; the payload itself is untouched."""
        if not isinstance(data, dict):
            return data
        payload = cast(dict[str, Any], data)
        names: dict[object, list[Any]] = {
            c.get("name"): _list(c.get("names")) for c in dicts(payload.get("categories"))
        }
        athletes: list[dict[str, Any]] = []
        for row in dicts(payload.get("athletes")):
            named: dict[str, Any] = {"athlete": row.get("athlete")}
            for category in dicts(row.get("categories")):
                name = category.get("name")
                if isinstance(name, str) and name != "athlete":
                    values = _list(category.get("values"))
                    named[name] = dict(zip(names.get(name, []), values, strict=False))
            athletes.append(named)
        return {"requestedSeason": payload.get("requestedSeason"), "athletes": athletes}


def _list(value: object) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []
