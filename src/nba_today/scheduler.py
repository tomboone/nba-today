"""The daily loop: plan the send time, sleep until it, send once (spec §3.2-3.3, ADR-0001)."""

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from nba_today.clock import Clock
from nba_today.digest.models import DigestKind
from nba_today.settings import Settings
from nba_today.sources.models.espn_scoreboard import EspnScoreboard, is_counted
from nba_today.state import Marker, State
from nba_today.transport.base import SendReceipt, TransportError

log = logging.getLogger(__name__)

RETRY_DELAY = timedelta(minutes=10)
# How soon to plan again when a scoreboard the plan needs was unavailable.
REPLAN_DELAY = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class Plan:
    """What to send on one day and when; both `None` on a day with nothing to send."""

    kind: DigestKind | None
    send_at: datetime | None


NOTHING = Plan(kind=None, send_at=None)


class Pipeline(Protocol):
    def scoreboards(self, day: date) -> tuple[EspnScoreboard | None, EspnScoreboard | None]:
        """The scoreboards for `day` and the day before; `None` where one is unavailable."""
        ...

    def send(self, day: date) -> SendReceipt:
        """Build and send the digest for `day`; raises `TransportError` if nothing delivered it."""
        ...


def tip_offs(scoreboard: EspnScoreboard) -> list[datetime]:
    """When each counted game tips off; a game ESPN sent without a time is left out."""
    return [event.date for event in scoreboard.events if is_counted(event) and event.date]


def plan(
    day: date,
    scoreboard_today: EspnScoreboard | None,
    scoreboard_yesterday: EspnScoreboard | None,
    settings: Settings,
) -> Plan:
    """A full digest at the earliest tip-off - `SEND_LEAD_MINUTES` on a game day; a scores-only
    digest at `SCORES_ONLY_TIME` local on the day after one; otherwise nothing.

    An unavailable `scoreboard_today` plans nothing: guessing would either skip a game day or send
    a scores-only digest on one.
    """
    if scoreboard_today is None:
        return NOTHING
    if tips := tip_offs(scoreboard_today):
        send_at = min(tips) - timedelta(minutes=settings.send_lead_minutes)
        return Plan(kind="full", send_at=send_at.astimezone(settings.tz_local))
    if scoreboard_yesterday is not None and tip_offs(scoreboard_yesterday):
        send_at = datetime.combine(day, settings.scores_only_time, tzinfo=settings.tz_local)
        return Plan(kind="scores_only", send_at=send_at)
    return NOTHING


def _decidable(
    scoreboard_today: EspnScoreboard | None, scoreboard_yesterday: EspnScoreboard | None
) -> bool:
    """Whether `plan()` had every scoreboard it needed; without them "nothing" is only a guess."""
    if scoreboard_today is None:
        return False
    return bool(tip_offs(scoreboard_today)) or scoreboard_yesterday is not None


class Scheduler:
    """Plans at boot and at every `PLAN_TIME`, and sends each day's digest at most once.

    The marker in `state` is what makes "at most once" survive a restart: a send time that has
    already passed fires only if the marker does not cover today. A plan made without the
    scoreboards it needs is retried every `REPLAN_DELAY`, so an outage at `PLAN_TIME` delays the
    day's digest rather than skipping it. A failed send is retried once after `RETRY_DELAY`.
    Setting `stop` ends the loop at the next sleep; a send already under way finishes and records
    its marker first.

    Only `TransportError` is handled. Anything else is a bug and propagates: the container restarts
    and the next boot retries, since no marker was written.
    """

    def __init__(
        self,
        clock: Clock,
        state: State,
        settings: Settings,
        pipeline: Pipeline,
        *,
        stop: threading.Event | None = None,
    ) -> None:
        self._clock = clock
        self._state = state
        self._settings = settings
        self._pipeline = pipeline
        self._stop = stop or threading.Event()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.cycle()

    def cycle(self) -> None:
        """Plan today, send if a send is due, then sleep until the next `PLAN_TIME`."""
        now = self._clock.now()
        today = now.astimezone(self._settings.tz_local).date()
        scoreboard_today, scoreboard_yesterday = self._pipeline.scoreboards(today)
        day_plan = plan(today, scoreboard_today, scoreboard_yesterday, self._settings)
        self._state.touch_planned(now)
        log.info(
            "planned",
            extra={"date": today.isoformat(), "kind": day_plan.kind, "send_at": day_plan.send_at},
        )
        if day_plan.kind is not None and day_plan.send_at is not None:
            self._dispatch(today, day_plan.kind, day_plan.send_at)
        wake = self._next_plan_time()
        if not _decidable(scoreboard_today, scoreboard_yesterday):
            wake = min(wake, self._clock.now() + REPLAN_DELAY)
            log.warning(
                "scoreboard unavailable, planning again",
                extra={"date": today.isoformat(), "at": wake},
            )
        self._sleep_until(wake)

    def _dispatch(self, day: date, kind: DigestKind, send_at: datetime) -> None:
        marker = self._state.read()
        if marker is not None and marker.date == day:
            log.info("already sent today, skipping", extra={"date": day.isoformat()})
            return
        if not self._sleep_until(send_at) or self._send(day, kind) is None:
            return
        log.warning("retrying send", extra={"date": day.isoformat(), "in": str(RETRY_DELAY)})
        if not self._sleep_until(self._clock.now() + RETRY_DELAY):
            return
        if self._send(day, kind) is not None:
            log.error(
                "send failed twice, giving up until the next plan",
                extra={"date": day.isoformat()},
            )

    def _send(self, day: date, kind: DigestKind) -> TransportError | None:
        """Send the digest for `day` and record it in the marker; the error if nothing delivered it.

        A failed send leaves the marker alone, so a restart will try again (spec §3.2).
        """
        try:
            receipt = self._pipeline.send(day)
        except TransportError as exc:
            log.error("send failed", extra={"date": day.isoformat(), "error": str(exc)})
            return exc
        previous = self._state.read()
        self._state.write(
            Marker(
                date=day,
                kind=kind,
                sent_at=receipt.sent_at,
                transport=receipt.transport,
                message_id=receipt.message_id,
                planned_at=previous.planned_at if previous else None,
            )
        )
        return None

    def _sleep_until(self, when: datetime) -> bool:
        """Sleep until `when`; False if `stop` was set, meaning the loop should wind down."""
        self._clock.sleep_until(when)
        return not self._stop.is_set()

    def _next_plan_time(self) -> datetime:
        tz = self._settings.tz_local
        now = self._clock.now().astimezone(tz)
        candidate = datetime.combine(now.date(), self._settings.plan_time, tzinfo=tz)
        if candidate <= now:
            candidate = datetime.combine(
                now.date() + timedelta(days=1), self._settings.plan_time, tzinfo=tz
            )
        return candidate
