# NBA Today

A daily NBA digest email, sent 30 minutes before the day's first tip-off.

nba-today is a single long-running Python service, deployed as one container on a self-hosted Ubuntu box. It
builds the digest from ESPN's API, enriched with NBA.com stats and broadcast data, and delivers it through Resend,
with SMTP as a fallback.

See [`CONTEXT.md`](CONTEXT.md) for the glossary and [`docs/adr/`](docs/adr/) for the design decisions.

## Commands

| Command | Does |
|---|---|
| `nba-today run` | Runs the scheduler loop (the container entrypoint) |
| `nba-today send [--date YYYY-MM-DD] [--dry-run]` | Builds and sends one digest now |
| `nba-today preview [--date YYYY-MM-DD] [--out digest.html] [--from-snapshots]` | Renders a digest to HTML and text files without sending |
| `nba-today health` | Exits 0 if the scheduler has been active in the last 26 hours |

## Running locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync
uv run nba-today --help
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## License

MIT; see [`LICENSE.md`](LICENSE.md).

Data: ESPN, NBA.com. Not affiliated with the NBA.
