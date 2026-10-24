import json
import os
import re
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from typer.testing import CliRunner

from nba_today.cli import app
from nba_today.scheduler import Scheduler
from nba_today.settings import Settings
from nba_today.sources.snapshot import SnapshotStore
from tests.mocked_sources import FakeNbaSession, mock_espn, seed_snapshots, use_nba_session

runner = CliRunner()

COMMANDS = ["run", "send", "preview", "health"]
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr("nba_today.sources.http.time.sleep", no_sleep)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A clean environment with recipients and a data volume under `tmp_path`; returns it."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("RECIPIENTS", "fan@example.com")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeNbaSession:
    fake = FakeNbaSession()
    use_nba_session(monkeypatch, fake)
    return fake


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    # Typer forces Rich styling under GITHUB_ACTIONS; strip it so word boundaries match.
    output = ANSI_ESCAPE.sub("", result.output)
    for command in COMMANDS:
        assert re.search(rf"\b{command}\b", output), command


@pytest.mark.parametrize("command", ["run", "send", "preview"])
def test_invalid_config_exits_2(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.delenv("RECIPIENTS", raising=False)

    result = runner.invoke(app, [command])

    assert result.exit_code == 2
    assert "invalid configuration" in result.output


def test_health_exits_1_not_2_on_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Docker reserves health-check exit code 2.
    monkeypatch.delenv("RECIPIENTS", raising=False)

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "invalid configuration" in result.output


@respx.mock
@pytest.mark.usefixtures("session")
def test_send_dry_run_writes_the_outbox_for_the_date(env: Path) -> None:
    mock_espn(respx.mock)

    result = runner.invoke(app, ["send", "--date", "2025-11-17", "--dry-run"])

    assert result.exit_code == 0, result.output
    html = env / "outbox" / "2025-11-17.html"
    assert "NBA Today" in html.read_text()
    assert (env / "outbox" / "2025-11-17.txt").exists()
    receipt = json.loads(result.output.splitlines()[-1])
    assert (receipt["transport"], receipt["message_id"]) == ("outbox", str(html))


@respx.mock
@pytest.mark.usefixtures("session")
def test_send_honours_dry_run_from_the_environment(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    mock_espn(respx.mock)

    result = runner.invoke(app, ["send", "--date", "2025-11-17"])

    assert result.exit_code == 0, result.output
    assert (env / "outbox" / "2025-11-17.html").exists()


def test_send_defaults_to_today_in_the_local_zone(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_snapshots(SnapshotStore(env))
    monkeypatch.setenv("NBA_ENRICHMENT", "false")

    def today() -> str:
        return datetime.now(UTC).astimezone(ZoneInfo("America/New_York")).date().isoformat()

    # Either side of the call, in case it straddles Eastern midnight.
    days = {today()}
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(503))
        result = runner.invoke(app, ["send", "--dry-run"])
    days.add(today())

    assert result.exit_code == 0, result.output
    assert any((env / "outbox" / f"{day}.html").exists() for day in days)


def test_send_without_a_configured_transport_exits_2_before_fetching(env: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        anything = router.route().mock(return_value=httpx.Response(500))
        result = runner.invoke(app, ["send", "--date", "2025-11-17"])

    assert result.exit_code == 2
    assert "no transport is configured" in result.output
    assert anything.call_count == 0


def test_preview_from_snapshots_writes_html_and_text_without_network(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_snapshots(SnapshotStore(env))
    session = FakeNbaSession(fail=True)
    use_nba_session(monkeypatch, session)
    out = env / "d.html"

    with respx.mock(assert_all_called=False) as router:
        anything = router.route().mock(return_value=httpx.Response(500))
        result = runner.invoke(
            app, ["preview", "--date", "2025-11-17", "--from-snapshots", "--out", str(out)]
        )

    assert result.exit_code == 0, result.output
    assert anything.call_count == 0
    assert session.calls == []
    assert "<html" in out.read_text()
    assert "NBA Today" in out.with_suffix(".txt").read_text()
    assert not (env / "outbox").exists()


@pytest.mark.usefixtures("env")
def test_run_without_a_configured_transport_exits_2_at_boot() -> None:
    with respx.mock(assert_all_called=False) as router:
        anything = router.route().mock(return_value=httpx.Response(500))
        result = runner.invoke(app, ["run"])

    assert result.exit_code == 2
    assert "no transport is configured" in result.output
    assert anything.call_count == 0


def test_run_drives_the_scheduler_until_a_signal_stops_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    handlers: dict[int, Callable[[int, FrameType | None], object]] = {}

    def install(signum: int, handler: Callable[[int, FrameType | None], object]) -> None:
        handlers[signum] = handler

    stopped: list[bool] = []

    def stop_at_once(scheduler: Scheduler) -> None:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        stopped.append(scheduler._stop.is_set())  # pyright: ignore[reportPrivateUsage]

    # Never install real handlers in the test process.
    monkeypatch.setattr("nba_today.cli.signal.signal", install)
    monkeypatch.setattr(Scheduler, "run_forever", stop_at_once)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0, result.output
    assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
    assert stopped == [True]
    assert '"msg": "scheduler stopped"' in result.output


def _state_file(data_dir: Path, age_hours: float) -> Path:
    path = data_dir / "state.json"
    path.write_text("{}")
    mtime = time.time() - age_hours * 3600
    os.utime(path, (mtime, mtime))
    return path


def test_health_exits_0_with_a_fresh_state_file(env: Path) -> None:
    _state_file(env, age_hours=1)

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0, result.output


def test_health_exits_1_with_a_27_hour_old_state_file(env: Path) -> None:
    _state_file(env, age_hours=27)

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "state file last updated 27.0 hours ago" in result.output


def test_health_exits_1_without_a_state_file(env: Path) -> None:
    result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "no state file" in result.output
