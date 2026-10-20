"""Record trimmed live ESPN and NBA.com responses into tests/fixtures/ (spec §10).

    uv run scripts/record_fixtures.py --date 2026-03-14 [RECORDER ...]

Each recorder is registered by name with `@recorder` and writes into `tests/fixtures/espn/` or
`tests/fixtures/nba/` through `write_fixture`. The source tickets add recorders as they add
clients; with no names given, every registered recorder runs.
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from nba_today.season import espn_season, season_label
from nba_today.settings import Settings
from nba_today.sources.espn import get_athletes, get_injuries, get_scoreboard, get_standings
from nba_today.sources.http import make_client
from nba_today.sources.nba import (
    Session,
    get_player_advanced,
    get_player_base,
    get_schedule,
    get_team_advanced,
)
from nba_today.sources.nba_http import make_nba_session

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

type Payload = dict[str, Any]
type Source = Literal["espn", "nba"]
type RecordFn = Callable[[date], None]


@dataclass(frozen=True, slots=True)
class Recorder:
    record: RecordFn
    help: str


RECORDERS: dict[str, Recorder] = {}


def recorder(name: str, help: str) -> Callable[[RecordFn], RecordFn]:
    """Register `fn` under `name`; `fn` receives the `--date` and writes its own fixtures."""

    def register(fn: RecordFn) -> RecordFn:
        RECORDERS[name] = Recorder(fn, help)
        return fn

    return register


def write_fixture(source: Source, name: str, payload: Payload) -> None:
    path = FIXTURES / source / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(FIXTURES.parent.parent)}")


def _default(field: str) -> str:
    return Settings.model_fields[field].default


SCOREBOARD_GAMES = 3
# Bulk the scoreboard carries on every event and team that spec §5.1 never reads.
SCOREBOARD_DROPPED_KEYS = frozenset(
    {"calendar", "geoBroadcasts", "headlines", "highlights", "links", "statistics"}
)


def _overtimes(event: Payload) -> int:
    return max(event["status"]["period"] - 4, 0)


SCOREBOARD_EDGE_CASES: tuple[Callable[[Payload], bool], ...] = (
    lambda e: _overtimes(e) == 1,
    lambda e: _overtimes(e) >= 2,
    lambda e: e["status"]["type"]["name"] == "STATUS_POSTPONED",
    # ESPN hangs a playoff series on a regular-season game between the same two teams.
    lambda e: any(c.get("series") for c in e["competitions"]),
)


def _drop_keys(value: Any, keys: frozenset[str]) -> Any:
    """`value` with every dict key in `keys` removed, at any depth."""
    if isinstance(value, dict):
        items = cast(Payload, value).items()
        return {k: _drop_keys(v, keys) for k, v in items if k not in keys}
    if isinstance(value, list):
        return [_drop_keys(v, keys) for v in cast(list[Any], value)]
    return value


def _headlines(event: Payload) -> tuple[str, ...]:
    return tuple(n.get("headline", "") for c in event["competitions"] for n in c.get("notes", []))


def _trim_events(events: list[Payload]) -> list[Payload]:
    """The first few events plus the first of each edge case they lack, in original order.

    Also the first event for each set of notes they lack, since `is_counted` reads the notes:
    the All-Star championship otherwise falls behind three round-robin games.
    """
    kept = set(range(min(SCOREBOARD_GAMES, len(events))))
    for is_case in SCOREBOARD_EDGE_CASES:
        if any(is_case(events[i]) for i in kept):
            continue
        first = next((i for i, e in enumerate(events) if is_case(e)), None)
        if first is not None:
            kept.add(first)
    seen = {_headlines(events[i]) for i in kept}
    for i, event in enumerate(events):
        if _headlines(event) not in seen:
            seen.add(_headlines(event))
            kept.add(i)
    return [e for i, e in enumerate(events) if i in kept]


@recorder(
    "espn_scoreboard",
    "ESPN scoreboard for --date, trimmed to a few games plus OT, 2OT, and postponed ones",
)
def record_espn_scoreboard(day: date) -> None:
    with make_client(_default("espn_site_api_base")) as client:
        payload = get_scoreboard(client, day)
    payload["events"] = _trim_events(payload.get("events", []))
    write_fixture("espn", f"scoreboard_{day:%Y%m%d}", _drop_keys(payload, SCOREBOARD_DROPPED_KEYS))


# Logos and links ride along on every team and athlete in the endpoints below.
LINK_KEYS = frozenset({"headshot", "links", "logos", "teamLogos"})


# Every stat carries four labels and a sentence besides its name; `seasons` lists every season.
STANDINGS_DROPPED_KEYS = LINK_KEYS | {
    "description",
    "displayName",
    "seasons",
    "shortDisplayName",
    "uid",
}


@recorder("espn_standings", "ESPN regular-season standings for the season of --date, all 30 teams")
def record_espn_standings(day: date) -> None:
    with make_client(_default("espn_site_api_base")) as client:
        payload = get_standings(client, day)
    write_fixture(
        "espn", f"standings_{espn_season(day)}", _drop_keys(payload, STANDINGS_DROPPED_KEYS)
    )


INJURY_TEAMS = 3
INJURY_DROPPED_KEYS = LINK_KEYS | {"longComment", "shortComment"}


def _listed_elsewhere(team: Payload) -> bool:
    """Whether the team lists a player whose own `athlete.team` is a different team."""
    return any(i["athlete"]["team"]["id"] != team["id"] for i in team["injuries"])


def _statuses(team: Payload) -> set[str]:
    return {i["status"] for i in team["injuries"]}


def _trim_injury_teams(teams: list[Payload]) -> list[Payload]:
    """The first few teams, plus the first to list each status they lack, plus the first one
    listing a player who is on another team, in original order."""
    kept = set(range(min(INJURY_TEAMS, len(teams))))
    seen = set[str]().union(*(_statuses(teams[i]) for i in kept))
    for i, team in enumerate(teams):
        if not _statuses(team) <= seen:
            seen |= _statuses(team)
            kept.add(i)
    if not any(_listed_elsewhere(teams[i]) for i in kept):
        first = next((i for i, t in enumerate(teams) if _listed_elsewhere(t)), None)
        if first is not None:
            kept.add(first)
    return [t for i, t in enumerate(teams) if i in kept]


@recorder(
    "espn_injuries",
    "ESPN injury report as of now (--date only picks the season), trimmed to a few teams",
)
def record_espn_injuries(day: date) -> None:
    with make_client(_default("espn_site_api_base")) as client:
        payload = get_injuries(client, day)
    payload["injuries"] = _trim_injury_teams(payload.get("injuries", []))
    write_fixture("espn", "injuries", _drop_keys(payload, INJURY_DROPPED_KEYS))


# The teams of the 2026-27 opening night (scoreboard_20261020), so key-player tests can pair
# the two fixtures; each team's leading scorers, in ESPN's points-per-game order.
ATHLETE_TEAMS = frozenset({"DET", "BOS", "NY", "PHI", "SA", "OKC"})
ATHLETES_PER_TEAM = 7
ATHLETE_DROPPED_KEYS = LINK_KEYS | {"glossary", "ranks", "totals"}


def _trim_athletes(athletes: list[Payload]) -> list[Payload]:
    per_team: dict[str, int] = {}
    kept: list[Payload] = []
    for athlete in athletes:
        team = athlete["athlete"].get("teamShortName")
        if team in ATHLETE_TEAMS and per_team.get(team, 0) < ATHLETES_PER_TEAM:
            per_team[team] = per_team.get(team, 0) + 1
            kept.append(athlete)
    return kept


@recorder(
    "espn_athletes",
    f"ESPN regular-season athlete stats for the season of --date, {ATHLETES_PER_TEAM} per team "
    f"for {len(ATHLETE_TEAMS)} teams",
)
def record_espn_athletes(day: date) -> None:
    with make_client(_default("espn_web_api_base")) as client:
        payload = get_athletes(client, day)
    payload["athletes"] = _trim_athletes(payload.get("athletes", []))
    write_fixture("espn", f"athletes_{espn_season(day)}", _drop_keys(payload, ATHLETE_DROPPED_KEYS))


# Dates kept from each season's schedule, chosen to pair with the ESPN scoreboard fixtures: a
# regular night with local TV, a Cup knockout, the Cup final, the last regular-season night, the
# first, second, and last play-in nights, playoff nights with `seriesText` (tied, 3-1 leads, a
# clinch), and the Finals clincher; then the 2026-27 opening night and the night after, with
# local TV and team OTT.
SCHEDULE_DATES = {
    "2025-26": frozenset(
        {
            date(2025, 11, 16),
            date(2025, 12, 10),
            date(2025, 12, 16),
            date(2026, 4, 12),
            date(2026, 4, 14),
            date(2026, 4, 15),
            date(2026, 4, 17),
            date(2026, 4, 20),
            date(2026, 4, 26),
            date(2026, 4, 27),
            date(2026, 6, 13),
        }
    ),
    "2026-27": frozenset({date(2026, 10, 20), date(2026, 10, 21)}),
}
# The CDN file holds only the current season; stats.nba.com serves past ones in the same shape.
PAST_SCHEDULE_URL = "/stats/scheduleleaguev2int"
SCHEDULE_DROPPED_KEYS = frozenset(
    {
        "broadcasterList",
        "intlOttBroadcasters",
        "intlRadioBroadcasters",
        "intlTvBroadcasters",
        "pointsLeaders",
        "weeks",
    }
)


def _get_json(session: Session, url: str, params: dict[str, str]) -> Payload:
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _schedule_date(game_date: Payload) -> date:
    return datetime.strptime(game_date["gameDate"], "%m/%d/%Y %H:%M:%S").date()


@recorder(
    "nba_schedule",
    "NBA.com season schedule for the season of --date, trimmed to that season's fixture dates "
    "(or --date alone for any other season)",
)
def record_nba_schedule(day: date) -> None:
    label = season_label(day)
    with make_nba_session() as session:
        payload = get_schedule(session, _default("nba_cdn_base"))
        if payload["leagueSchedule"]["seasonYear"] != label:
            url = f"{_default('nba_stats_base')}{PAST_SCHEDULE_URL}"
            payload = _get_json(session, url, {"LeagueID": "00", "Season": label})
    kept = SCHEDULE_DATES.get(label, frozenset({day}))
    schedule = payload["leagueSchedule"]
    schedule["gameDates"] = [d for d in schedule["gameDates"] if _schedule_date(d) in kept]
    write_fixture("nba", f"schedule_{label}", _drop_keys(payload, SCHEDULE_DROPPED_KEYS))


@recorder(
    "nba_team_advanced",
    "stats.nba.com advanced team dashboard for the regular season of --date, all 30 teams",
)
def record_nba_team_advanced(day: date) -> None:
    with make_nba_session() as session:
        payload = get_team_advanced(session, _default("nba_stats_base"), day)
    write_fixture("nba", f"team_advanced_{season_label(day)}", payload)


# The NBA tricodes of the ESPN athlete fixture's teams, so the two sources' players can be paired.
PLAYER_TEAMS = frozenset({"DET", "BOS", "NYK", "PHI", "SAS", "OKC"})
PLAYERS_PER_TEAM = 7


def _rows(payload: Payload) -> tuple[list[str], list[list[Any]]]:
    result_set = payload["resultSets"][0]
    return result_set["headers"], result_set["rowSet"]


def _top_scorers(base: Payload) -> set[int]:
    """The ids of each team's leading scorers by points per game, from the base dashboard."""
    headers, rows = _rows(base)
    players = [dict(zip(headers, row, strict=True)) for row in rows]
    players.sort(key=lambda r: r["PTS"], reverse=True)
    per_team: dict[str, int] = {}
    kept: set[int] = set()
    for player in players:
        team = player["TEAM_ABBREVIATION"]
        if team in PLAYER_TEAMS and per_team.get(team, 0) < PLAYERS_PER_TEAM:
            per_team[team] = per_team.get(team, 0) + 1
            kept.add(player["PLAYER_ID"])
    return kept


def _keep_players(payload: Payload, ids: set[int]) -> Payload:
    headers, rows = _rows(payload)
    player_id = headers.index("PLAYER_ID")
    payload["resultSets"][0]["rowSet"] = [r for r in rows if r[player_id] in ids]
    return payload


@recorder(
    "nba_players",
    f"stats.nba.com base and advanced player dashboards for the regular season of --date, the "
    f"same {PLAYERS_PER_TEAM} leading scorers per team for {len(PLAYER_TEAMS)} teams in both",
)
def record_nba_players(day: date) -> None:
    stats_base = _default("nba_stats_base")
    with make_nba_session() as session:
        base = get_player_base(session, stats_base, day)
        advanced = get_player_advanced(session, stats_base, day)
    ids = _top_scorers(base)
    label = season_label(day)
    write_fixture("nba", f"player_base_{label}", _keep_players(base, ids))
    write_fixture("nba", f"player_advanced_{label}", _keep_players(advanced, ids))


def _registry() -> str:
    if not RECORDERS:
        return "recorders:\n  (none registered yet)"
    width = max(map(len, RECORDERS))
    lines = [f"  {name:<{width}}  {r.help}" for name, r in sorted(RECORDERS.items())]
    return "recorders:\n" + "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record live fixtures.",
        epilog=_registry(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "recorders", nargs="*", metavar="RECORDER", help="recorders to run (default: all)"
    )
    args = parser.parse_args()
    day: date = args.date
    names: list[str] = args.recorders or sorted(RECORDERS)

    unknown = [n for n in names if n not in RECORDERS]
    if unknown:
        parser.error(f"unknown recorder(s): {', '.join(unknown)}")
    if not names:
        parser.error("no recorders registered yet")
    for name in names:
        RECORDERS[name].record(day)


if __name__ == "__main__":
    main()
