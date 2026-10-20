"""Source payloads → the digest's sections (spec §6). Pure: no I/O, and no networking imports.

ESPN and NBA.com name games and teams differently, so a game is matched across them on
(Eastern date, home tricode, away tricode) through `teams.py`, never on tip-off time: the two
sources' times can differ by minutes, and the NBA plays no doubleheaders.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from nba_today import teams
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import (
    Broadcasts,
    GameLeaders,
    InjuryReport,
    KeyPlayer,
    Leader,
    Matchup,
    MatchupSide,
    Result,
    ResultSide,
    TeamRatings,
)
from nba_today.digest.qualify import is_qualified
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_athletes import AthleteStats, EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import (
    REGULAR_SEASON,
    Broadcast,
    Competition,
    Competitor,
    Event,
    LeaderCategory,
    StatusType,
    is_counted,
)
from nba_today.sources.models.espn_standings import Entry, EspnStandings
from nba_today.sources.models.nba_schedule import Broadcaster, Broadcasters, NbaSchedule
from nba_today.sources.models.nba_schedule import Game as NbaGame
from nba_today.sources.models.nba_schedule import Team as NbaTeam
from nba_today.sources.models.nba_team_stats import TeamAdvanced

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
LEAGUE_PASS = "NBA League Pass"
LABEL_SEPARATOR = " · "
# A regular-season game keeps its label only in the NBA Cup knockouts: the group-play note
# (`NBA Cup - Group Play`) and one-offs such as `NBA Mexico City Game 2025` show nothing.
CUP_LABEL = "NBA Cup"
CUP_GROUP_LABEL = "Group"
OUT = "Out"
DAY_TO_DAY = "Day-To-Day"
FINAL = "STATUS_FINAL"
POSTPONED = "STATUS_POSTPONED"
RESULT_FINAL = "final"
RESULT_POSTPONED = "postponed"
REGULATION_PERIODS = 4

type Standings = dict[str, Entry]


@dataclass(frozen=True, slots=True)
class _ByTeam:
    """What each matchup side reads from the sources beyond the scoreboard, keyed by tricode.

    A team missing from a mapping, or a source that is unavailable, leaves that field at its
    empty default: an empty injury report, no ratings, no key player.
    """

    standings: Standings
    injuries: dict[str, InjuryReport]
    ratings: dict[str, TeamRatings]
    key_players: dict[str, KeyPlayer]


def build_matchups(day: date, bundle: SourceBundle) -> list[Matchup]:
    """Today's counted games, sorted by tip-off, then home tricode.

    Empty when today's scoreboard is unavailable. Only games on `day` (Eastern) are kept: a stale
    snapshot can be an earlier day's scoreboard. A game with no tip-off, or with a team that is
    not in `teams.py`, is logged and skipped.
    """
    if bundle.espn_scoreboard_today is None:
        return []
    schedule = None if bundle.nba_schedule is None else bundle.nba_schedule.data
    standings = {} if bundle.espn_standings is None else _standings(bundle.espn_standings.data)
    by_team = _ByTeam(
        standings=standings,
        injuries=_injury_reports(bundle.espn_injuries),
        ratings=_ratings(bundle.nba_team_advanced),
        key_players=_key_players(bundle.espn_athletes, standings),
    )
    matchups: list[Matchup] = []
    for event in bundle.espn_scoreboard_today.data.events:
        if not is_counted(event) or event.date is None:
            continue
        if event.date.astimezone(EASTERN).date() != day:
            continue
        try:
            matchups.append(_matchup(event, schedule, by_team))
        except teams.UnknownTeam:
            log.warning("skipping game with an unknown team", extra={"event": event.id})
    return sorted(matchups, key=lambda m: (m.tip_off, m.home.tricode))


def build_results(day: date, bundle: SourceBundle) -> list[Result]:
    """Yesterday's counted games, those on the day before `day` (Eastern), in tip-off order.

    Empty when yesterday's scoreboard is unavailable or had no counted game. As with matchups, a
    stale snapshot can be an earlier day's scoreboard, so other days' games are dropped. A game
    with no status, or with a team that is not in `teams.py`, is logged and skipped.
    """
    if bundle.espn_scoreboard_yesterday is None:
        return []
    schedule = None if bundle.nba_schedule is None else bundle.nba_schedule.data
    yesterday = day - timedelta(days=1)
    events: list[tuple[datetime, Event]] = [
        (event.date, event)
        for event in bundle.espn_scoreboard_yesterday.data.events
        if is_counted(event)
        and event.date is not None
        and event.date.astimezone(EASTERN).date() == yesterday
    ]
    results: list[Result] = []
    for _, event in sorted(events, key=lambda pair: pair[0]):
        try:
            result = _result(event, schedule)
        except teams.UnknownTeam:
            log.warning("skipping result with an unknown team", extra={"event": event.id})
            continue
        if result is None:
            log.warning("skipping result with no status", extra={"event": event.id})
            continue
        results.append(result)
    return results


def match_nba_game(event: Event, schedule: NbaSchedule) -> NbaGame | None:
    """The NBA.com game for an ESPN event, on (Eastern date, home tricode, away tricode)."""
    if event.date is None or schedule.league_schedule is None:
        return None
    try:
        home, away = (_team(event, side).tricode for side in ("home", "away"))
    except teams.UnknownTeam:
        return None
    day = event.date.astimezone(EASTERN).date()
    for game_date in schedule.league_schedule.game_dates:
        if game_date.game_date != day:
            continue
        for game in game_date.games:
            if (_tricode(game.home_team), _tricode(game.away_team)) == (home, away):
                return game
    return None


def _matchup(event: Event, schedule: NbaSchedule | None, by_team: _ByTeam) -> Matchup:
    assert event.date is not None
    competition = _competition(event)
    nba_game = None if schedule is None else match_nba_game(event, schedule)
    venue = competition.venue
    address = None if venue is None else venue.address
    tv = (
        _nba_broadcasts(nba_game.broadcasters)
        if nba_game is not None and nba_game.broadcasters is not None
        else _espn_broadcasts(competition.broadcasts)
    )
    return Matchup(
        tip_off=event.date,
        label=_label(event, competition, nba_game),
        arena=None if venue is None else venue.full_name,
        city=None if address is None else address.city,
        away=_side(event, "away", by_team),
        home=_side(event, "home", by_team),
        tv=tv,
    )


def _side(event: Event, home_away: str, by_team: _ByTeam) -> MatchupSide:
    """Records from the scoreboard, else the standings; last-10 and streak from the standings;
    injuries, ratings, and key player from their own sources.

    The NBA Cup final carries no record on the scoreboard, since it does not count in the
    standings.
    """
    tricode = _team(event, home_away).tricode
    entry = by_team.standings.get(tricode)
    record = _record(_competitor(event, home_away))
    if record is None and entry is not None:
        record = (entry.wins, entry.losses)
    wins, losses = record or (None, None)
    return MatchupSide(
        tricode=tricode,
        wins=wins,
        losses=losses,
        last_ten=None if entry is None else entry.last_ten_games,
        streak=None if entry is None else entry.streak,
        injuries=by_team.injuries.get(tricode, InjuryReport()),
        ratings=by_team.ratings.get(tricode),
        key_player=by_team.key_players.get(tricode),
    )


def _record(competitor: Competitor | None) -> tuple[int, int] | None:
    """`records[type=total].summary`, e.g. `"7-7"`, as (wins, losses)."""
    if competitor is None:
        return None
    for record in competitor.records:
        if record.type == "total" and record.summary:
            wins, _, losses = record.summary.partition("-")
            try:
                return int(wins), int(losses)
            except ValueError:
                return None
    return None


def _label(event: Event, competition: Competition, nba_game: NbaGame | None) -> str | None:
    """ESPN's first note, else NBA.com's label and sub-label; on a regular-season game, Cup
    knockouts only."""
    label = competition.notes[0].headline if competition.notes else None
    if label is None and nba_game is not None:
        parts = [p for p in (nba_game.game_label, nba_game.game_sub_label) if p]
        label = LABEL_SEPARATOR.join(parts) or None
    if label is None:
        return None
    season_type = None if event.season is None else event.season.type
    if season_type == REGULAR_SEASON and (CUP_LABEL not in label or CUP_GROUP_LABEL in label):
        return None
    return label


def _result(event: Event, schedule: NbaSchedule | None) -> Result | None:
    """The game's result, or `None` if ESPN sent neither a status name nor a detail.

    Overtimes are the periods past the fourth. A game that never tipped off (postponed, or any
    other status at period 0) has no score or leaders: ESPN sends `"0"` placeholders for both
    sides.
    """
    status = event.status
    status_type = None if status is None else status.type
    result_status = None if status_type is None else _result_status(status_type)
    if status is None or result_status is None:
        return None
    period = status.period or 0
    played = result_status != RESULT_POSTPONED and period > 0
    nba_game = None if schedule is None else match_nba_game(event, schedule)
    return Result(
        status=result_status,
        overtimes=max(period - REGULATION_PERIODS, 0),
        label=_label(event, _competition(event), nba_game),
        away=_result_side(event, "away", played),
        home=_result_side(event, "home", played),
    )


def _result_status(status_type: StatusType) -> str | None:
    """`final`, `postponed`, or any other status as ESPN details it."""
    if status_type.name == FINAL:
        return RESULT_FINAL
    if status_type.name == POSTPONED:
        return RESULT_POSTPONED
    return status_type.detail or status_type.name


def _result_side(event: Event, home_away: str, played: bool) -> ResultSide:
    tricode = _team(event, home_away).tricode
    competitor = _competitor(event, home_away)
    if not played or competitor is None:
        return ResultSide(tricode=tricode, score=None)
    return ResultSide(
        tricode=tricode, score=competitor.score, leaders=_game_leaders(competitor.leaders)
    )


def _game_leaders(categories: list[LeaderCategory]) -> GameLeaders | None:
    """Each category's top player; a category ESPN left out is `None`, and so is the whole when
    ESPN sent none of them."""
    leaders = {category.name: _leader(category) for category in categories}
    game_leaders = GameLeaders(
        points=leaders.get("points"),
        rebounds=leaders.get("rebounds"),
        assists=leaders.get("assists"),
    )
    return None if game_leaders == GameLeaders() else game_leaders


def _leader(category: LeaderCategory) -> Leader | None:
    top = category.leaders[0] if category.leaders else None
    if top is None or top.value is None or top.athlete is None:
        return None
    if top.athlete.display_name is None:
        return None
    return Leader(name=top.athlete.display_name, value=int(top.value))


def _nba_broadcasts(broadcasters: Broadcasters) -> Broadcasts:
    b = broadcasters
    return Broadcasts(
        national=_names(b.national_tv_broadcasters + b.national_ott_broadcasters),
        home=_names(b.home_tv_broadcasters + b.home_ott_broadcasters),
        away=_names(b.away_tv_broadcasters + b.away_ott_broadcasters),
    )


def _names(broadcasters: list[Broadcaster]) -> list[str]:
    return _dedupe([b.broadcaster_display for b in broadcasters if b.broadcaster_display])


def _espn_broadcasts(broadcasts: list[Broadcast]) -> Broadcasts:
    """ESPN's markets map to the same buckets; League Pass is on every game, so it is dropped."""
    buckets: dict[str, list[str]] = {"national": [], "home": [], "away": []}
    for broadcast in broadcasts:
        if broadcast.market in buckets:
            buckets[broadcast.market] += [n for n in broadcast.names if n != LEAGUE_PASS]
    return Broadcasts(
        national=_dedupe(buckets["national"]),
        home=_dedupe(buckets["home"]),
        away=_dedupe(buckets["away"]),
    )


def _dedupe(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


def _standings(standings: EspnStandings) -> Standings:
    """Each team's standings entry by tricode; an entry with an unknown team is logged and
    skipped."""
    by_tricode: Standings = {}
    for conference in standings.children:
        if conference.standings is None:
            continue
        for entry in conference.standings.entries:
            espn_id = None if entry.team is None else entry.team.id
            try:
                tricode = teams.by_espn_id(espn_id).tricode if espn_id is not None else None
            except teams.UnknownTeam:
                tricode = None
            if tricode is None:
                log.warning("skipping standings entry with an unknown team", extra={"id": espn_id})
                continue
            by_tricode[tricode] = entry
    return by_tricode


def _injury_reports(injuries: Fetched[EspnInjuries] | None) -> dict[str, InjuryReport]:
    """Each team's report, in ESPN's order within a status; a status other than Out or
    Day-To-Day goes to `other` verbatim.

    A player is matched to a team through their own `athlete.team`, not the group ESPN lists them
    under: the two can disagree. An entry with no name, no status, or no known team is skipped.
    """
    if injuries is None:
        return {}
    out: dict[str, list[str]] = {}
    day_to_day: dict[str, list[str]] = {}
    other: dict[str, list[tuple[str, str]]] = {}
    for group in injuries.data.injuries:
        for injury in group.injuries:
            athlete = injury.athlete
            if athlete is None or athlete.display_name is None or injury.status is None:
                continue
            team = athlete.team
            tricode = None if team is None else _espn_tricode(team.id, team.abbreviation)
            if tricode is None:
                continue
            if injury.status == OUT:
                out.setdefault(tricode, []).append(athlete.display_name)
            elif injury.status == DAY_TO_DAY:
                day_to_day.setdefault(tricode, []).append(athlete.display_name)
            else:
                other.setdefault(tricode, []).append((athlete.display_name, injury.status))
    return {
        tricode: InjuryReport(
            out=out.get(tricode, []),
            day_to_day=day_to_day.get(tricode, []),
            other=other.get(tricode, []),
        )
        for tricode in out.keys() | day_to_day.keys() | other.keys()
    }


def _ratings(team_advanced: Fetched[list[TeamAdvanced]] | None) -> dict[str, TeamRatings]:
    """Each team's ratings by tricode; a row missing a rating or naming an unknown team is
    skipped."""
    if team_advanced is None:
        return {}
    ratings: dict[str, TeamRatings] = {}
    for row in team_advanced.data:
        if (
            row.team_id is None
            or row.off_rating is None
            or row.def_rating is None
            or row.net_rating is None
            or row.pace is None
        ):
            continue
        try:
            tricode = teams.by_nba_id(row.team_id).tricode
        except teams.UnknownTeam:
            log.warning("skipping team ratings with an unknown team", extra={"id": row.team_id})
            continue
        ratings[tricode] = TeamRatings(
            off_rtg=row.off_rating, def_rtg=row.def_rating, net_rtg=row.net_rating, pace=row.pace
        )
    return ratings


def _key_players(
    athletes: Fetched[EspnAthletes] | None, standings: Standings
) -> dict[str, KeyPlayer]:
    """Each team's qualified player with the highest PPG; ties break on RPG, then name.

    Team games played are the standings' wins plus losses, keeping key players ESPN-only. A team
    with no qualified player, or missing from the standings, has no entry.
    """
    if athletes is None:
        return {}
    candidates: dict[str, list[KeyPlayer]] = {}
    for row in athletes.data.athletes:
        qualified = _qualified_player(row, standings)
        if qualified is not None:
            tricode, player = qualified
            candidates.setdefault(tricode, []).append(player)
    return {
        tricode: min(players, key=lambda p: (-p.ppg, -p.rpg, p.name))
        for tricode, players in candidates.items()
    }


def _qualified_player(row: AthleteStats, standings: Standings) -> tuple[str, KeyPlayer] | None:
    """The athlete's tricode and line if they qualify; `None` if not, or if a stat is missing."""
    athlete, general, offensive = row.athlete, row.general, row.offensive
    if athlete is None or general is None or offensive is None or athlete.display_name is None:
        return None
    gp, mpg, rpg = general.games_played, general.avg_minutes, general.avg_rebounds
    ppg, apg = offensive.avg_points, offensive.avg_assists
    if gp is None or mpg is None or rpg is None or ppg is None or apg is None:
        return None
    tricode = _espn_tricode(athlete.team_id, athlete.team_short_name)
    entry = None if tricode is None else standings.get(tricode)
    if tricode is None or entry is None or entry.wins is None or entry.losses is None:
        return None
    if not is_qualified(gp, mpg, entry.wins + entry.losses):
        return None
    return tricode, KeyPlayer(name=athlete.display_name, ppg=ppg, rpg=rpg, apg=apg)


def _espn_tricode(espn_id: int | None, abbreviation: str | None) -> str | None:
    """The tricode for an ESPN team id, else its abbreviation; `None` if neither is known.

    An unknown team is logged; a missing one (an athlete with no team) is not.
    """
    try:
        if espn_id is not None:
            return teams.by_espn_id(espn_id).tricode
        if abbreviation is not None:
            return teams.by_espn_abbr(abbreviation).tricode
    except teams.UnknownTeam:
        log.warning(
            "skipping a row with an unknown team",
            extra={"id": espn_id, "abbreviation": abbreviation},
        )
    return None


def _competition(event: Event) -> Competition:
    return event.competitions[0] if event.competitions else Competition()


def _competitor(event: Event, home_away: str) -> Competitor | None:
    competitors = _competition(event).competitors
    return next((c for c in competitors if c.home_away == home_away), None)


def _team(event: Event, home_away: str) -> teams.Team:
    competitor = _competitor(event, home_away)
    espn_id = None if competitor is None or competitor.team is None else competitor.team.id
    if espn_id is None:
        raise teams.UnknownTeam(f"no {home_away} team id on event {event.id}")
    return teams.by_espn_id(espn_id)


def _tricode(team: NbaTeam | None) -> str | None:
    return None if team is None else team.team_tricode
