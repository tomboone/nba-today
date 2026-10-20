"""Source payloads → the digest's sections (spec §6). Pure: no I/O, and no networking imports.

ESPN and NBA.com name games and teams differently, so a game is matched across them on
(Eastern date, home tricode, away tricode) through `teams.py`, never on tip-off time: the two
sources' times can differ by minutes, and the NBA plays no doubleheaders.
"""

import logging
from datetime import date
from zoneinfo import ZoneInfo

from nba_today import teams
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import Broadcasts, Matchup, MatchupSide
from nba_today.sources.models.espn_scoreboard import (
    REGULAR_SEASON,
    Broadcast,
    Competition,
    Competitor,
    Event,
    is_counted,
)
from nba_today.sources.models.espn_standings import Entry, EspnStandings
from nba_today.sources.models.nba_schedule import Broadcaster, Broadcasters, NbaSchedule
from nba_today.sources.models.nba_schedule import Game as NbaGame
from nba_today.sources.models.nba_schedule import Team as NbaTeam

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
LEAGUE_PASS = "NBA League Pass"
LABEL_SEPARATOR = " · "
# A regular-season game keeps its label only in the NBA Cup knockouts: the group-play note
# (`NBA Cup - Group Play`) and one-offs such as `NBA Mexico City Game 2025` show nothing.
CUP_LABEL = "NBA Cup"
CUP_GROUP_LABEL = "Group"

type Standings = dict[str, Entry]


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
    matchups: list[Matchup] = []
    for event in bundle.espn_scoreboard_today.data.events:
        if not is_counted(event) or event.date is None:
            continue
        if event.date.astimezone(EASTERN).date() != day:
            continue
        try:
            matchups.append(_matchup(event, schedule, standings))
        except teams.UnknownTeam:
            log.warning("skipping game with an unknown team", extra={"event": event.id})
    return sorted(matchups, key=lambda m: (m.tip_off, m.home.tricode))


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


def _matchup(event: Event, schedule: NbaSchedule | None, standings: Standings) -> Matchup:
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
        away=_side(event, "away", standings),
        home=_side(event, "home", standings),
        tv=tv,
    )


def _side(event: Event, home_away: str, standings: Standings) -> MatchupSide:
    """Records from the scoreboard, else the standings; last-10 and streak from the standings.

    The NBA Cup final carries no record on the scoreboard, since it does not count in the
    standings.
    """
    tricode = _team(event, home_away).tricode
    entry = standings.get(tricode)
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
