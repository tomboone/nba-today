"""The digest → the email: subject, MJML-compiled HTML, and a plain-text alternative (spec §6).

Pure: Jinja2 renders the MJML and text templates from the `Digest`, and `mjml` (the Python port
of the compiler, ADR-0003) turns the MJML into mail-client HTML. No I/O beyond the packaged
templates.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import mjml
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from nba_today.digest.build import RESULT_FINAL, RESULT_POSTPONED
from nba_today.digest.models import Clinch, Digest, Result

# Every time in the digest reads in Eastern, like the game day it belongs to.
EASTERN = ZoneInfo("America/New_York")
SOURCE_LABELS = {
    "espn_scoreboard_today": "ESPN schedule",
    "espn_scoreboard_yesterday": "ESPN scores",
    "espn_standings": "ESPN standings",
    "espn_injuries": "ESPN injuries",
    "espn_athletes": "ESPN player stats",
    "nba_schedule": "NBA.com schedule",
    "nba_team_advanced": "NBA.com team ratings",
    # The leaderboard joins both; the banner collapses them into one line.
    "nba_player_advanced": "NBA.com player stats",
    "nba_player_base": "NBA.com player stats",
}
# Without any of these the leaderboard cannot be built, so its section is dropped silently (the
# banner already says why) rather than claiming nobody qualifies.
LEADERBOARD_SOURCES = frozenset({"nba_team_advanced", "nba_player_advanced", "nba_player_base"})
# The marks NBA standings print beside a team, legend included.
CLINCH_MARKS = {
    Clinch.BEST_RECORD: "z",
    Clinch.DIVISION: "y",
    Clinch.PLAYOFF: "x",
    Clinch.PLAY_IN: "pi",
    Clinch.ELIMINATED: "e",
}
CLINCH_LEGEND = "z best record · y division · x playoffs · pi play-in · e eliminated"
# A standings line falls under these seeds: the last playoff seed, then the last play-in seed.
LINE_SEEDS = frozenset({6, 10})


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    html: str
    text: str


def render(digest: Digest) -> Rendered:
    """The digest's subject, HTML, and text alternative.

    Raises `ValueError` when the MJML does not compile.
    """
    title = subject(digest)
    context = {
        "digest": digest,
        "subject": title,
        "stale": _stale(digest),
        "unavailable": list(dict.fromkeys(_label(name) for name in digest.unavailable)),
        "no_qualified_players": digest.leaderboard is None
        and LEADERBOARD_SOURCES.isdisjoint(digest.unavailable),
        "clinch_legend": CLINCH_LEGEND if _any_clinched(digest) else "",
        "line_seeds": LINE_SEEDS,
    }
    source = _ENV.get_template("digest.mjml.j2").render(context)
    return Rendered(
        subject=title,
        html=mjml.mjml2html(source, disable_comments=True),
        text=_ENV.get_template("digest.txt.j2").render(context),
    )


def subject(digest: Digest) -> str:
    title = "NBA Today" if digest.kind == "full" else "NBA Scores"
    return f"{title} — {digest.date:%b} {digest.date.day}"


def ampm(when: datetime) -> str:
    """`7:30 PM`, in Eastern."""
    local = when.astimezone(EASTERN)
    return f"{local:%I:%M %p}".removeprefix("0")


def stamp(when: datetime) -> str:
    """`Oct 19, 8:00 AM ET`."""
    local = when.astimezone(EASTERN)
    return f"{local:%b} {local.day}, {ampm(local)} ET"


def long_date(day: date) -> str:
    return f"{day:%A, %B} {day.day}, {day.year}"


def one(value: float) -> str:
    """A per-game average or rating to one decimal: `28.7`."""
    return f"{value:.1f}"


def signed(value: float) -> str:
    """A net rating with its sign: `+8.3`, `-9.7`, `0.0`."""
    return f"{value:+.1f}" if round(value, 1) else "0.0"


def percent(value: float) -> str:
    """A 0-1 rate as a percentage to one decimal, sign omitted: `0.626` → `62.6`."""
    return f"{value * 100:.1f}"


def record(wins: int | None, losses: int | None) -> str:
    """`4-9`, or empty when the record is unknown."""
    return "" if wins is None or losses is None else f"{wins}-{losses}"


def overtime(overtimes: int) -> str:
    """`OT`, `2OT`, or empty for regulation."""
    if overtimes == 0:
        return ""
    return "OT" if overtimes == 1 else f"{overtimes}OT"


def status(result: Result) -> str:
    """A result's status the way the email names it: `Postponed`, or ESPN's detail."""
    return "Postponed" if result.status == RESULT_POSTPONED else result.status


def score_note(result: Result) -> str:
    """What follows a score: `OT`/`2OT`, plus any status beyond a plain final. Empty for a
    regulation final."""
    parts = [overtime(result.overtimes)] if result.overtimes else []
    if result.status != RESULT_FINAL:
        parts.append(status(result))
    return ", ".join(parts)


def clinch_mark(clinch: Clinch | None) -> str:
    return "" if clinch is None else CLINCH_MARKS[clinch]


def _any_clinched(digest: Digest) -> bool:
    return any(row.clinch for c in digest.standings or [] for row in c.rows)


def _label(name: str) -> str:
    return SOURCE_LABELS.get(name, name)


def _stale(digest: Digest) -> list[tuple[str, datetime]]:
    """Each stale source's label and snapshot time; collapsed labels keep their oldest time."""
    oldest: dict[str, datetime] = {}
    for name, when in digest.stale.items():
        label = _label(name)
        oldest[label] = min(when, oldest.get(label, when))
    return list(oldest.items())


_ENV = Environment(
    loader=PackageLoader("nba_today.render", "templates"),
    autoescape=select_autoescape(enabled_extensions=("mjml.j2",), default_for_string=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_FILTERS: dict[str, Callable[..., object]] = {
    "ampm": ampm,
    "stamp": stamp,
    "long_date": long_date,
    "one": one,
    "signed": signed,
    "percent": percent,
    "record": record,
    "overtime": overtime,
    "status": status,
    "score_note": score_note,
    "clinch_mark": clinch_mark,
}
_ENV.filters.update(_FILTERS)
