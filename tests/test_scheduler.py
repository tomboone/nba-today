import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from nba_today.scheduler import NOTHING, REPLAN_DELAY, RETRY_DELAY, Plan, Scheduler, plan
from nba_today.settings import Settings
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from nba_today.state import Marker, State
from nba_today.transport.base import AllTransportsFailed, SendReceipt, TransportError
from tests.fakes import FakeClock

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 12)
YESTERDAY = DAY - timedelta(days=1)
NO_GAMES = EspnScoreboard()
REGULAR_SEASON = 2
PRESEASON = 1


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def _event(tip_off: datetime | None, season_type: int, note: str | None) -> dict[str, Any]:
    return {
        "date": None if tip_off is None else tip_off.astimezone(UTC).isoformat(),
        "season": {"type": season_type},
        "competitions": [{"notes": [] if note is None else [{"headline": note}]}],
    }


def scoreboard(
    *tip_offs: datetime | None, season_type: int = REGULAR_SEASON, note: str | None = None
) -> EspnScoreboard:
    """A scoreboard with one game per tip-off, in ESPN's UTC `date` form."""
    events = [_event(tip_off, season_type, note) for tip_off in tip_offs]
    return EspnScoreboard.model_validate({"events": events})


class StubPipeline:
    """Scoreboards by date, and a send that records when it ran and fails the first `failures`."""

    def __init__(
        self, clock: FakeClock, scoreboards: dict[date, EspnScoreboard | None], failures: int = 0
    ) -> None:
        self.clock = clock
        self.by_date = scoreboards
        self.failures = failures
        self.sends: list[datetime] = []

    def scoreboards(self, day: date) -> tuple[EspnScoreboard | None, EspnScoreboard | None]:
        yesterday = day - timedelta(days=1)
        return self.by_date.get(day, NO_GAMES), self.by_date.get(yesterday, NO_GAMES)

    def send(self, day: date) -> SendReceipt:
        self.sends.append(self.clock.now())
        if self.failures:
            self.failures -= 1
            raise AllTransportsFailed([("resend", TransportError("503"))])
        return SendReceipt(transport="resend", message_id=f"msg-{day}", sent_at=self.clock.now())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate({"recipients": ["fan@example.com"], "data_dir": tmp_path})


@pytest.fixture
def state(settings: Settings) -> State:
    return State(settings.data_dir / "state.json")


def scheduler(
    clock: FakeClock,
    state: State,
    settings: Settings,
    pipeline: StubPipeline,
    stop: threading.Event | None = None,
) -> Scheduler:
    return Scheduler(clock, state, settings, pipeline, stop=stop)


# plan()


def test_plan_game_day_sends_lead_minutes_before_the_earliest_tip_off(
    settings: Settings,
) -> None:
    today = scoreboard(at(19, 30), at(19), at(22))

    assert plan(DAY, today, NO_GAMES, settings) == Plan(kind="full", send_at=at(18, 30))


def test_plan_honours_send_lead_minutes(settings: Settings) -> None:
    settings = settings.model_copy(update={"send_lead_minutes": 45})

    assert plan(DAY, scoreboard(at(19)), NO_GAMES, settings).send_at == at(18, 15)


def test_plan_early_tip_abroad_sends_at_half_past_noon(settings: Settings) -> None:
    assert plan(DAY, scoreboard(at(13), at(19)), NO_GAMES, settings) == Plan("full", at(12, 30))


def test_plan_day_after_a_game_day_is_scores_only(settings: Settings) -> None:
    yesterday = scoreboard(at(19, day=YESTERDAY))

    assert plan(DAY, NO_GAMES, yesterday, settings) == Plan("scores_only", at(9, 0))


def test_plan_no_games_either_day_is_nothing(settings: Settings) -> None:
    assert plan(DAY, NO_GAMES, NO_GAMES, settings) == NOTHING


def test_plan_ignores_games_that_are_not_counted(settings: Settings) -> None:
    preseason = scoreboard(at(19, day=YESTERDAY), season_type=PRESEASON)
    all_star = scoreboard(at(20), note="NBA All-Star - Championship")

    assert plan(DAY, all_star, preseason, settings) == NOTHING


def test_plan_earliest_tip_off_is_among_counted_games_only(settings: Settings) -> None:
    # A regular-season game, one ESPN sent without a time, and an earlier All-Star game.
    counted = scoreboard(at(19), None)
    all_star = scoreboard(at(15), note="NBA All-Star - Rising Stars")
    today = EspnScoreboard(events=[*counted.events, *all_star.events])

    assert plan(DAY, today, NO_GAMES, settings) == Plan("full", at(18, 30))


def test_plan_unknown_today_is_nothing_even_after_a_game_day(settings: Settings) -> None:
    assert plan(DAY, None, scoreboard(at(19, day=YESTERDAY)), settings) == NOTHING


def test_plan_unknown_yesterday_on_a_no_game_day_is_nothing(settings: Settings) -> None:
    assert plan(DAY, NO_GAMES, None, settings) == NOTHING


# Scheduler


def test_boot_before_send_time_sends_once_at_send_time(state: State, settings: Settings) -> None:
    clock = FakeClock(at(10))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == [at(18, 30)]
    marker = state.read()
    assert marker is not None
    assert (marker.date, marker.kind, marker.transport) == (DAY, "full", "resend")
    assert (marker.sent_at, marker.planned_at) == (at(18, 30), at(10))
    # Then it sleeps until the next day's plan.
    assert clock.now() == at(3, 0, DAY + timedelta(days=1))

    # A restart later the same day finds the marker and does not send again.
    clock.current = at(18, 40)
    scheduler(clock, state, settings, pipeline).cycle()
    assert pipeline.sends == [at(18, 30)]


def test_boot_after_send_time_with_todays_marker_does_not_send(
    state: State, settings: Settings
) -> None:
    sent = Marker(date=DAY, kind="full", sent_at=at(18, 30), transport="resend", message_id="m")
    state.write(sent)
    clock = FakeClock(at(20))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == []
    assert state.read() == sent.model_copy(update={"planned_at": at(20)})


def test_boot_after_send_time_without_marker_sends_immediately(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(20))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == [at(20)]
    marker = state.read()
    assert marker is not None
    assert marker.date == DAY


def test_yesterdays_marker_does_not_block_today(state: State, settings: Settings) -> None:
    state.write(Marker(date=YESTERDAY, kind="full", sent_at=at(18, 30, YESTERDAY)))
    clock = FakeClock(at(20))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == [at(20)]
    marker = state.read()
    assert marker is not None
    assert (marker.date, marker.sent_at) == (DAY, at(20))


def test_no_games_today_after_a_game_day_sends_scores_only_at_nine(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {YESTERDAY: scoreboard(at(19, day=YESTERDAY))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == [at(9)]
    marker = state.read()
    assert marker is not None
    assert (marker.date, marker.kind) == (DAY, "scores_only")


def test_no_games_either_day_sleeps_until_the_next_plan(state: State, settings: Settings) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == []
    assert clock.sleeps == [at(3, 0, DAY + timedelta(days=1))]
    assert state.read() == Marker(planned_at=at(3))


def test_early_tip_abroad_sends_at_half_past_noon(state: State, settings: Settings) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(13), at(19, 30))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert clock.sleeps[0] == at(12, 30)
    assert pipeline.sends == [at(12, 30)]


def test_unavailable_scoreboard_sends_nothing_and_plans_again_soon(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {DAY: None, YESTERDAY: scoreboard(at(19, day=YESTERDAY))})

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == []
    assert clock.sleeps == [at(3) + REPLAN_DELAY]


def test_unavailable_yesterday_on_a_no_game_day_plans_again_soon(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {YESTERDAY: None})

    scheduler(clock, state, settings, pipeline).cycle()

    assert clock.sleeps == [at(3) + REPLAN_DELAY]


def test_outage_at_plan_time_delays_the_digest_rather_than_skipping_it(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(3))
    pipeline = StubPipeline(clock, {DAY: None})
    loop = scheduler(clock, state, settings, pipeline)

    loop.cycle()
    pipeline.by_date[DAY] = scoreboard(at(19))
    loop.cycle()

    assert pipeline.sends == [at(18, 30)]


def test_replan_never_overshoots_the_next_plan_time(state: State, settings: Settings) -> None:
    clock = FakeClock(at(2, 50))
    pipeline = StubPipeline(clock, {DAY: None})

    scheduler(clock, state, settings, pipeline).cycle()

    assert clock.sleeps == [at(3)]


def test_boot_before_plan_time_replans_at_plan_time(state: State, settings: Settings) -> None:
    clock = FakeClock(at(1))
    pipeline = StubPipeline(clock, {})

    scheduler(clock, state, settings, pipeline).cycle()

    assert clock.sleeps == [at(3)]


def test_failed_send_leaves_no_marker_and_retries_once(state: State, settings: Settings) -> None:
    clock = FakeClock(at(10))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))}, failures=2)

    scheduler(clock, state, settings, pipeline).cycle()

    assert pipeline.sends == [at(18, 30), at(18, 30) + RETRY_DELAY]
    # Only the plan touched the file; nothing records a send.
    assert state.read() == Marker(planned_at=at(10))


def test_failed_send_then_successful_retry_writes_the_marker(
    state: State, settings: Settings
) -> None:
    clock = FakeClock(at(10))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))}, failures=1)

    scheduler(clock, state, settings, pipeline).cycle()

    marker = state.read()
    assert marker is not None
    assert (marker.date, marker.sent_at) == (DAY, at(18, 40))


def test_run_forever_returns_once_stopped(state: State, settings: Settings) -> None:
    stop = threading.Event()
    stop.set()
    clock = FakeClock(at(10))
    pipeline = StubPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline, stop).run_forever()

    assert (pipeline.sends, state.read()) == ([], None)


def test_stop_during_a_send_finishes_it_and_records_the_marker(
    state: State, settings: Settings
) -> None:
    stop = threading.Event()
    clock = FakeClock(at(10))

    class StoppingPipeline(StubPipeline):
        def send(self, day: date) -> SendReceipt:
            stop.set()
            return super().send(day)

    pipeline = StoppingPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline, stop).run_forever()

    assert pipeline.sends == [at(18, 30)]
    marker = state.read()
    assert marker is not None
    assert marker.date == DAY


def test_stop_before_send_time_skips_the_send(state: State, settings: Settings) -> None:
    stop = threading.Event()
    clock = FakeClock(at(10))

    class StoppingPipeline(StubPipeline):
        def scoreboards(self, day: date) -> tuple[EspnScoreboard | None, EspnScoreboard | None]:
            stop.set()
            return super().scoreboards(day)

    pipeline = StoppingPipeline(clock, {DAY: scoreboard(at(19))})

    scheduler(clock, state, settings, pipeline, stop).run_forever()

    assert pipeline.sends == []
