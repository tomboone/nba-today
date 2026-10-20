"""Command-line entry point (spec §9)."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from nba_today.logging import configure_logging
from nba_today.settings import Settings

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


@app.command()
def run() -> None:
    """Run the scheduler loop (container entrypoint)."""
    _bootstrap()
    _not_implemented("run")


@app.command()
def send(
    day: DateOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Write to the outbox instead of sending.")
    ] = False,
) -> None:
    """Build and send one digest now."""
    _bootstrap()
    _not_implemented("send")


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
    _bootstrap()
    _not_implemented("preview")


@app.command()
def health() -> None:
    """Exit 0 if the scheduler touched the state file in the last 26 hours, else 1."""
    try:
        _bootstrap()
    except typer.Exit as exc:
        # Docker reserves health-check exit code 2; a config the scheduler cannot load is unhealthy.
        raise typer.Exit(1) from exc
    _not_implemented("health")
