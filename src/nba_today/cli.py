"""Command-line entry point (spec §9)."""

import json
import logging
import signal
import threading
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from types import FrameType
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from nba_today.clock import SystemClock
from nba_today.logging import configure_logging
from nba_today.pipeline import DigestPipeline, build, run_send
from nba_today.scheduler import Scheduler
from nba_today.settings import Settings
from nba_today.state import State
from nba_today.transport.base import TransportError
from nba_today.transport.factory import TransportConfigError, build_transport

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger(__name__)


def _bootstrap() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        configure_logging()
        log.error("invalid configuration", extra={"errors": exc.errors(include_url=False)})
        raise typer.Exit(2) from exc
    configure_logging(settings.log_level)
    return settings


def _not_implemented(command: str) -> NoReturn:
    log.error("command not implemented yet", extra={"command": command})
    raise typer.Exit(1)


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
    Scheduler(SystemClock(stop), state, settings, DigestPipeline(settings), stop=stop).run_forever()
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
        _bootstrap()
    except typer.Exit as exc:
        # Docker reserves health-check exit code 2; a config the scheduler cannot load is unhealthy.
        raise typer.Exit(1) from exc
    _not_implemented("health")
