"""What every ESPN payload model shares: camelCase aliases, and numbers that tolerate strings.

ESPN sends numbers such as team ids and scores as strings, which coerce; a blank one becomes
None (spec §5.0).
"""

from typing import Annotated, Any, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


type LaxInt = Annotated[int | None, BeforeValidator(_blank_to_none)]
type LaxFloat = Annotated[float | None, BeforeValidator(_blank_to_none)]


# Each model module subclasses BaseModel itself with this config, rather than sharing a base
# class: ruff's RUF012 only sees a pydantic model through bases defined in the same file.
ESPN_CONFIG = ConfigDict(alias_generator=to_camel, frozen=True)


class Team(BaseModel):
    model_config = ESPN_CONFIG

    id: LaxInt = None
    abbreviation: str | None = None


def dicts(value: object) -> list[dict[str, Any]]:
    """The dict items of a raw list, for the validators that reshape a payload before parsing."""
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], v) for v in cast(list[Any], value) if isinstance(v, dict)]
