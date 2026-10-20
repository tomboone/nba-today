import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nba_today.state import Marker, State

PLANNED = datetime(2026, 9, 23, 7, tzinfo=UTC)
SENT = Marker(
    date=date(2026, 9, 22),
    kind="full",
    sent_at=datetime(2026, 9, 22, 16, 35, tzinfo=UTC),
    transport="resend",
    message_id="msg-1",
    planned_at=datetime(2026, 9, 22, 7, tzinfo=UTC),
)


def test_missing_file_is_none(tmp_path: Path) -> None:
    assert State(tmp_path / "state.json").read() is None


def test_round_trip_creates_the_directory(tmp_path: Path) -> None:
    state = State(tmp_path / "data" / "state.json")

    state.write(SENT)

    assert state.read() == SENT


def test_file_has_the_shared_contract_fields(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")

    state.write(SENT)

    assert state.path.read_text() == (
        '{"date":"2026-09-22","kind":"full","sent_at":"2026-09-22T16:35:00Z",'
        '"transport":"resend","message_id":"msg-1","planned_at":"2026-09-22T07:00:00Z"}'
    )


def test_touch_planned_on_a_fresh_volume_records_only_the_plan(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")

    state.touch_planned(PLANNED)

    assert state.read() == Marker(planned_at=PLANNED)


def test_touch_planned_keeps_the_last_send(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    state.write(SENT)

    state.touch_planned(PLANNED)

    assert state.read() == SENT.model_copy(update={"planned_at": PLANNED})


def test_writes_leave_no_temp_files(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")

    state.write(SENT)
    state.touch_planned(PLANNED)

    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_unreadable_file_is_none_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")

    with caplog.at_level(logging.ERROR):
        assert State(path).read() is None

    assert "unreadable state file" in caplog.messages
