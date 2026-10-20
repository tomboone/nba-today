"""NBA.com client: the season schedule (spec §5.2), for local TV, streaming, and game labels.

NBA.com is enrichment (ADR-0004): with `NBA_ENRICHMENT=false` every fetch returns None without a
request, and the digest treats the source as unavailable. Each function takes any object with a
`get(url, params)` method, so tests pass a fake: respx cannot intercept curl_cffi. Production
passes `nba_http.make_nba_session()`.

The schedule is one static file for the whole season, and takes no season parameter. A file
labelled with any season other than the one `today` falls in does not parse, so it never
replaces a good snapshot: around the August rollover the CDN may still serve the old season.
"""

from datetime import date
from typing import Any, Protocol

from nba_today.season import season_label
from nba_today.settings import Settings
from nba_today.sources.fetched import Fetched, fetch_or_snapshot
from nba_today.sources.models.nba_schedule import NbaSchedule
from nba_today.sources.snapshot import Payload, SnapshotStore

SCHEDULE_PATH = "/static/json/staticData/scheduleLeagueV2_1.json"
SCHEDULE_SOURCE = "nba_schedule"


class Response(Protocol):
    def raise_for_status(self) -> object: ...

    def json(self) -> Any: ...


class Session(Protocol):
    def get(self, url: str, params: dict[str, str] | None = None) -> Response: ...


class WrongSeason(ValueError):
    def __init__(self, expected: str, got: str | None) -> None:
        super().__init__(f"schedule is for season {got}, expected {expected}")


def _get(session: Session, url: str, params: dict[str, str] | None = None) -> Payload:
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()


def get_schedule(session: Session, cdn_base: str) -> Payload:
    """The raw season schedule, about 7.5 MB; raises on an error status."""
    return _get(session, f"{cdn_base}{SCHEDULE_PATH}")


def parse_schedule(payload: Payload, today: date) -> NbaSchedule:
    """Parse the schedule; raise `WrongSeason` unless it is for the season `today` falls in."""
    schedule = NbaSchedule.model_validate(payload)
    expected = season_label(today)
    got = schedule.league_schedule.season_year if schedule.league_schedule else None
    if got != expected:
        raise WrongSeason(expected, got)
    return schedule


def fetch_schedule(
    session: Session | None, store: SnapshotStore, settings: Settings, today: date
) -> Fetched[NbaSchedule] | None:
    """The season schedule, or its snapshot; None when enrichment is off.

    With no session, the snapshot alone (`preview --from-snapshots`).
    """
    if not settings.nba_enrichment:
        return None
    fetch = None if session is None else lambda: get_schedule(session, settings.nba_cdn_base)
    return fetch_or_snapshot(
        SCHEDULE_SOURCE, fetch, lambda payload: parse_schedule(payload, today), store
    )
