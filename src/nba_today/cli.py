"""Command-line entry point (spec §9)."""

import json
import logging
import signal
import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from nba_today.clock import SystemClock
from nba_today.heartbeat import Heartbeat
from nba_today.logging import configure_logging
from nba_today.pipeline import DigestPipeline, build, run_send
from nba_today.scheduler import Scheduler
from nba_today.settings import Settings
from nba_today.sources.http import USER_AGENT
from nba_today.state import State
from nba_today.transport.base import TransportError
from nba_today.transport.factory import TransportConfigError, build_transport

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger(__name__)

# The scheduler touches the state file at least daily; 2 h of slack covers a late plan (spec §3.4).
HEALTHY_STATE_AGE = timedelta(hours=26)


def _bootstrap() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        configure_logging()
        log.error("invalid configuration", extra={"errors": exc.errors(include_url=False)})
        raise typer.Exit(2) from exc
    configure_logging(settings.log_level)
    return settings


# A plain alias, not `type`: Typer cannot see through a TypeAliasType.
DateOption = Annotated[
    datetime | None,
    typer.Option("--date", formats=["%Y-%m-%d"], help="Digest date; defaults to today."),
]


def _day(option: datetime | None, settings: Settings) -> date:
    """The `--date` given, else today in `TZ_LOCAL`."""
    if option is not None:
        return option.date()
    return SystemClock().now().astimezone(settings.tz_local).date()


@app.command()
def run() -> None:
    """Run the scheduler loop (container entrypoint)."""
    settings = _bootstrap()
    try:
        # Fail at boot, not at the first send hours later, when nothing could ever be sent.
        build_transport(settings)
    except TransportConfigError as exc:
        log.error("invalid configuration", extra={"error": str(exc)})
        raise typer.Exit(2) from exc
    stop = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    # Docker stops a container with SIGTERM; finish any send under way, then exit 0.
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, on_signal)
    state = State(settings.data_dir / "state.json")
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as http:
        heartbeat = Heartbeat(settings.healthchecks_url, http)
        pipeline = DigestPipeline(settings)
        Scheduler(SystemClock(stop), state, settings, pipeline, heartbeat, stop=stop).run_forever()
    log.info("scheduler stopped")


@app.command()
def send(
    day: DateOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Write to the outbox instead of sending.")
    ] = False,
) -> None:
    """Build and send one digest now."""
    settings = _bootstrap()
    if dry_run:
        settings = settings.model_copy(update={"dry_run": True})
    digest_day = _day(day, settings)
    try:
        receipt = run_send(digest_day, settings)
    except TransportConfigError as exc:
        log.error("invalid configuration", extra={"error": str(exc)})
        raise typer.Exit(2) from exc
    except TransportError as exc:
        log.error("send failed", extra={"date": digest_day.isoformat(), "error": str(exc)})
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(asdict(receipt), default=str))


@app.command()
def preview(
    day: DateOption = None,
    out: Annotated[
        Path, typer.Option("--out", help="HTML file; the text alternative goes beside it.")
    ] = Path("digest.html"),
    from_snapshots: Annotated[
        bool, typer.Option("--from-snapshots", help="Read stored snapshots only; no network.")
    ] = False,
) -> None:
    """Render a digest to a file without sending."""
    settings = _bootstrap()
    _, rendered = build(_day(day, settings), settings, offline=from_snapshots)
    text_out = out.with_suffix(".txt")
    out.write_text(rendered.html)
    text_out.write_text(rendered.text)
    typer.echo(f"{out}\n{text_out}")


@app.command()
def health() -> None:
    """Exit 0 if the scheduler touched the state file in the last 26 hours, else 1."""
    try:
        settings = _bootstrap()
    except typer.Exit as exc:
        # Docker reserves health-check exit code 2; a config the scheduler cannot load is unhealthy.
        raise typer.Exit(1) from exc
    path = settings.data_dir / "state.json"
    try:
        age = timedelta(seconds=time.time() - path.stat().st_mtime)
    except FileNotFoundError:
        typer.echo(f"unhealthy: no state file at {path}")
        raise typer.Exit(1) from None
    if age > HEALTHY_STATE_AGE:
        hours = age / timedelta(hours=1)
        typer.echo(f"unhealthy: state file last updated {hours:.1f} hours ago")
        raise typer.Exit(1)
