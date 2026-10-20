"""One digest, end to end: fetch every source → build → render → send (spec §3.2 step 2)."""

import logging
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from datetime import date

import httpx

from nba_today.digest.build import build_digest
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import Digest
from nba_today.render.render import Rendered, render
from nba_today.settings import Settings
from nba_today.sources import espn, nba
from nba_today.sources.fetched import Fetched, SourceUnavailable
from nba_today.sources.http import make_client
from nba_today.sources.nba_http import make_nba_session
from nba_today.sources.snapshot import SnapshotStore
from nba_today.transport.base import Message, SendReceipt
from nba_today.transport.factory import build_transport

log = logging.getLogger(__name__)

type Clients = tuple[httpx.Client | None, httpx.Client | None, nba.Session | None]


def fetch_bundle(day: date, settings: Settings, *, offline: bool = False) -> SourceBundle:
    """Every source the digest for `day` needs, each fresh or from its snapshot, one at a time.

    `offline` reads the snapshots alone, with no network (`preview --from-snapshots`). A source
    with no snapshot either is logged and left `None`, so one source failing never aborts the
    others; each source logs one line saying how it went and how long it took.
    """
    store = SnapshotStore(settings.data_dir)
    with _clients(settings, offline=offline) as (site, web, session):
        return SourceBundle(
            espn_scoreboard_today=_timed(
                espn.SCOREBOARD_SOURCE["today"],
                lambda: espn.fetch_scoreboard(site, store, day, "today"),
            ),
            espn_scoreboard_yesterday=_timed(
                espn.SCOREBOARD_SOURCE["yesterday"],
                lambda: espn.fetch_scoreboard(site, store, day, "yesterday"),
            ),
            espn_standings=_timed(
                espn.STANDINGS_SOURCE, lambda: espn.fetch_standings(site, store, day)
            ),
            espn_injuries=_timed(
                espn.INJURIES_SOURCE, lambda: espn.fetch_injuries(site, store, day)
            ),
            espn_athletes=_timed(
                espn.ATHLETES_SOURCE, lambda: espn.fetch_athletes(web, store, day)
            ),
            nba_schedule=_timed(
                nba.SCHEDULE_SOURCE, lambda: nba.fetch_schedule(session, store, settings, day)
            ),
            nba_team_advanced=_timed(
                nba.TEAM_ADVANCED_SOURCE,
                lambda: nba.fetch_team_advanced(session, store, settings, day),
            ),
            nba_player_advanced=_timed(
                nba.PLAYER_ADVANCED_SOURCE,
                lambda: nba.fetch_player_advanced(session, store, settings, day),
            ),
            nba_player_base=_timed(
                nba.PLAYER_BASE_SOURCE,
                lambda: nba.fetch_player_base(session, store, settings, day),
            ),
        )


def build(day: date, settings: Settings, *, offline: bool = False) -> tuple[Digest, Rendered]:
    """The digest for `day` and its rendering; `offline` reads snapshots only, with no network."""
    digest = build_digest(day, fetch_bundle(day, settings, offline=offline))
    return digest, render(digest)


def run_send(day: date, settings: Settings) -> SendReceipt:
    """Build the digest for `day` and send it to `RECIPIENTS`; the transport's receipt.

    Raises `TransportConfigError` before fetching anything when no transport is configured, and
    `TransportError` (`AllTransportsFailed` from a chain) when nothing delivered the digest.
    """
    transport = build_transport(settings, day=day)
    digest, rendered = build(day, settings)
    message = Message(
        subject=rendered.subject,
        html=rendered.html,
        text=rendered.text,
        to=settings.recipients,
        # Every real transport sends from its own configured sender; this is informational.
        from_=settings.resend_from or settings.smtp_sender_address or "",
    )
    receipt = transport.send(message)
    log.info(
        "digest sent",
        extra={
            "date": day.isoformat(),
            "kind": digest.kind,
            "stale": sorted(digest.stale),
            "unavailable": digest.unavailable,
            "transport": receipt.transport,
            "message_id": receipt.message_id,
        },
    )
    return receipt


def _timed[T](source: str, fetch: Callable[[], Fetched[T] | None]) -> Fetched[T] | None:
    """`fetch()`, or None when the source is unavailable; logs the outcome and its duration.

    The line is a warning when the source fell back to its snapshot or had none. An NBA.com fetch
    returns None by itself when `NBA_ENRICHMENT` is off; that is deliberate, so it logs at info.
    """
    started = time.monotonic()
    failed = False
    try:
        fetched = fetch()
    except SourceUnavailable:
        fetched, failed = None, True
    status = "unavailable" if fetched is None else "stale" if fetched.stale else "ok"
    log.log(
        logging.WARNING if failed or status == "stale" else logging.INFO,
        "source fetched",
        extra={
            "source": source,
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return fetched


@contextmanager
def _clients(settings: Settings, *, offline: bool) -> Generator[Clients]:
    """ESPN's site and web clients and the NBA.com session; each None when it must not be used.

    Offline, all three are None, so every fetch reads its snapshot. With `NBA_ENRICHMENT` off the
    session is never opened.
    """
    if offline:
        yield None, None, None
        return
    with ExitStack() as stack:
        site = stack.enter_context(make_client(settings.espn_site_api_base))
        web = stack.enter_context(make_client(settings.espn_web_api_base))
        session = stack.enter_context(make_nba_session()) if settings.nba_enrichment else None
        yield site, web, session
