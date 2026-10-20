"""What every stats.nba.com dashboard row model shares (spec §5.2).

Each model is one row of `resultSets[0]`, after `nba.parse_result_set` has zipped it with the
headers. Fields are the headers spec §5.2 reads, lower-cased (`OFF_RATING` → `off_rating`); every
other column is ignored. stats.nba.com sends numbers as numbers, and `null` where a rate has no
denominator; a blank or string number is tolerated all the same (spec §5.0).

On 2026-10-20, opening night, every 2026-27 dashboard had its full headers and no rows: a season
appears only once its first game is final.
"""

from typing import Annotated

from pydantic import BeforeValidator, ConfigDict


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


type LaxInt = Annotated[int | None, BeforeValidator(_blank_to_none)]
type LaxFloat = Annotated[float | None, BeforeValidator(_blank_to_none)]

STATS_CONFIG = ConfigDict(alias_generator=str.upper, frozen=True)
