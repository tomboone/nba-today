"""Source payloads → the digest's sections (spec §6). Pure: no I/O, and no networking imports.

ESPN and NBA.com name games and teams differently, so a game is matched across them on
(Eastern date, home tricode, away tricode) through `teams.py`, never on tip-off time: the two
sources' times can differ by minutes, and the NBA plays no doubleheaders.
"""

import logging
import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from nba_today import teams
from nba_today.digest.bundle import SourceBundle
from nba_today.digest.models import (
    Broadcasts,
    Clinch,
    ConferencePicture,
    ConferenceStandings,
    Digest,
    GameLeaders,
    InjuryReport,
    KeyPlayer,
    Leader,
    Leaderboard,
    LeaderRow,
    Matchup,
    MatchupSide,
    Phase,
    PlayInGame,
    PlayInSide,
    PlayoffRound,
    PlayoffSeries,
    PostseasonPicture,
    PostseasonTeam,
    Result,
    ResultSide,
    SeriesState,
    StandingsRow,
    TeamRatings,
)
from nba_today.digest.qualify import is_qualified
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_athletes import AthleteStats, EspnAthletes
from nba_today.sources.models.espn_injuries import EspnInjuries
from nba_today.sources.models.espn_scoreboard import (
    PLAY_IN,
    POSTSEASON,
    POSTSEASON_SEASON_TYPES,
    REGULAR_SEASON,
    Broadcast,
    Competition,
    Competitor,
    EspnScoreboard,
    Event,
    LeaderCategory,
    Series,
    StatusType,
    is_counted,
)
from nba_today.sources.models.espn_standings import Entry, EspnStandings
from nba_today.sources.models.nba_player_stats import PlayerAdvanced, PlayerBase
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
LEADER_GB = "-"
# ESPN's playoff note, `East 1st Round - Game 4`, and NBA.com's `seriesGameNumber`, `Game 4`.
ROUND_AND_GAME = re.compile(r"(?P<round>.+) - Game (?P<game>\d+)")
GAME_NUMBER = re.compile(r"Game (?P<game>\d+)")
# ESPN's play-in note, `NBA Play-In - East - 7th Place vs 8th Place`.
PLAY_IN_NOTE = re.compile(r"NBA Play-In - (?:East|West) - (?P<label>.+)")
PLAY_IN_LABEL = "Play-In"
# A first-round pairing's seeds sum to nine: 1 v 8, 2 v 7, 3 v 6, 4 v 5.
FIRST_ROUND_SEED_SUM = 9
# Fetched only in the postseason phase, so its absence in the regular season is no gap.
POSTSEASON_SOURCE = "espn_postseason"
LEADERBOARD_SIZE = 25
CONFERENCES: tuple[teams.Conference, ...] = ("East", "West")
# ESPN's `clincher` codes, pinned against the final 2025-26 standings: `z` marks the best record
# in a conference (DET) and `*` the best in the league (OKC), and both are the best record here;
# `xp` (seeds 7-8) and `pb` (seeds 9-10) are both a play-in berth.
CLINCH_CODES = {
    "z": Clinch.BEST_RECORD,
    "*": Clinch.BEST_RECORD,
    "y": Clinch.DIVISION,
    "x": Clinch.PLAYOFF,
    "xp": Clinch.PLAY_IN,
    "pb": Clinch.PLAY_IN,
    "e": Clinch.ELIMINATED,
}

type Standings = dict[str, Entry]


@dataclass(slots=True)
class _Series:
    """One playoff series as its games up to the digest date describe it."""

    # As ESPN listed game 1: home, then away.
    tricodes: tuple[str, str]
    # `None` for the Finals, whose teams come from both conferences.
    conference: teams.Conference | None
    start: datetime
    # ESPN's round, `East 1st Round` or `NBA Finals`, from the first game that names one.
    round_label: str | None = None
    # After the latest game played before the digest date.
    summary: str | None = None
    # Whether any game tipped off before the digest date, whether or not ESPN sent its state.
    played: bool = False


@dataclass(slots=True)
class _Postseason:
    play_in: dict[teams.Conference, list[PlayInGame]] = field(
        default_factory=lambda: {c: [] for c in CONFERENCES}
    )
    series: dict[frozenset[str], _Series] = field(default_factory=lambda: {})


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


def build_digest(day: date, bundle: SourceBundle) -> Digest:
    """Every section, plus which sources fell back to a snapshot and which had none.

    A day with matchups is a full digest; one without is scores-only.
    In the postseason the postseason picture takes the standings' place.
    """
    phase = build_phase(day, bundle)
    postseason = phase == "postseason"
    sources = [
        (f.name, getattr(bundle, f.name))
        for f in fields(bundle)
        if postseason or f.name != POSTSEASON_SOURCE
    ]
    matchups = build_matchups(day, bundle)
    return Digest(
        date=day,
        kind="full" if matchups else "scores_only",
        phase=phase,
        stale={name: f.fetched_at for name, f in sources if f is not None and f.stale},
        unavailable=[name for name, f in sources if f is None],
        matchups=matchups,
        results=build_results(day, bundle),
        standings=None if postseason else build_standings(bundle),
        postseason=build_postseason(day, bundle) if postseason else None,
        leaderboard=build_leaderboard(bundle),
    )


def build_phase(day: date, bundle: SourceBundle) -> Phase:
    """Postseason when any counted game today or yesterday is a play-in or playoff game.

    Read from the same games the matchups and results show, so a stale snapshot of another day
    does not count. With neither scoreboard available, the phase is regular. The rule needs no
    memory across off days: a day with no game today or yesterday sends no digest at all.
    """
    games = _games_on(bundle.espn_scoreboard_today, day) + _games_on(
        bundle.espn_scoreboard_yesterday, day - timedelta(days=1)
    )
    postseason = any(_season_type(event) in POSTSEASON_SEASON_TYPES for event in games)
    return "postseason" if postseason else "regular"


def build_matchups(day: date, bundle: SourceBundle) -> list[Matchup]:
    """Today's counted games, sorted by tip-off, then home tricode.

    Empty when today's scoreboard is unavailable. Only games on `day` (Eastern) are kept: a stale
    snapshot can be an earlier day's scoreboard. A game with no tip-off, or with a team that is
    not in `teams.py`, is logged and skipped.
    """
    games = _games_on(bundle.espn_scoreboard_today, day)
    if not games:
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
    for event in games:
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
    schedule = None if bundle.nba_schedule is None else bundle.nba_schedule.data
    games = _games_on(bundle.espn_scoreboard_yesterday, day - timedelta(days=1))
    results: list[Result] = []
    for event in sorted(games, key=_tip_off):
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


def build_standings(bundle: SourceBundle) -> list[ConferenceStandings] | None:
    """East, then West, each in seed order; `None` when the standings are unavailable.

    ESPN sends entries unsorted, and before a season's first game every team is seed 1, so seeds
    tie-break on tricode. An entry with no seed or record is logged and skipped.
    """
    if bundle.espn_standings is None:
        return None
    rows: dict[teams.Conference, list[StandingsRow]] = {c: [] for c in CONFERENCES}
    for tricode, entry in _standings(bundle.espn_standings.data).items():
        row = _standings_row(tricode, entry)
        if row is None:
            log.warning("skipping standings entry with no seed or record", extra={"team": tricode})
            continue
        rows[teams.by_tricode(tricode).conference].append(row)
    return [
        ConferenceStandings(conference=c, rows=sorted(rows[c], key=lambda r: (r.seed, r.tricode)))
        for c in CONFERENCES
    ]


def build_postseason(day: date, bundle: SourceBundle) -> PostseasonPicture | None:
    """The play-in games and each conference's playoff series by round, up to `day`.

    `None` when the postseason source is unavailable or holds no game on or before `day`. Games
    before `day` give scores and series state; `day`'s own have not tipped off at send time, so
    they only add a play-in game or a series that starts tonight. The play-in is listed until a
    playoff game has been played. A game with no tip-off or an unknown team is skipped.
    """
    if bundle.espn_postseason is None:
        return None
    postseason = _Postseason()
    events = [e for e in bundle.espn_postseason.data.events if e.date is not None]
    for event in sorted(events, key=_tip_off):
        tip_off = _tip_off(event).astimezone(EASTERN).date()
        if tip_off > day:
            continue
        try:
            _add_postseason_game(postseason, event, played=tip_off < day)
        except teams.UnknownTeam:
            log.warning("skipping postseason game with an unknown team", extra={"event": event.id})
    series = list(postseason.series.values())
    if not series and not any(postseason.play_in.values()):
        return None
    started = any(s.played for s in series)
    rounds = {c: _rounds([s for s in series if s.conference == c]) for c in CONFERENCES}
    first_rounds = [s for c in CONFERENCES for r in rounds[c][:1] for s in r]
    seeds = _playoff_seeds(first_rounds, bundle.espn_standings)
    finals = next((s for s in series if s.conference is None), None)
    return PostseasonPicture(
        finals=None if finals is None else _playoff_series(finals, seeds),
        conferences=[
            ConferencePicture(
                conference=c,
                rounds=[
                    PlayoffRound(
                        label=_round_name(r[0], c),
                        series=sorted(
                            (_playoff_series(s, seeds) for s in r),
                            key=lambda s: (s.high.seed is None, s.high.seed, s.high.tricode),
                        ),
                    )
                    for r in reversed(rounds[c])
                ],
                play_in=[] if started else postseason.play_in[c],
            )
            for c in CONFERENCES
        ],
    )


def _add_postseason_game(postseason: _Postseason, event: Event, played: bool) -> None:
    """File a play-in game, or fold a playoff game into its series; `played` if before today."""
    home, away = _team(event, "home"), _team(event, "away")
    competition = _competition(event)
    if _season_type(event) == PLAY_IN:
        postseason.play_in[home.conference].append(_play_in_game(event, competition, played))
        return
    if _season_type(event) != POSTSEASON:
        return
    key = frozenset({home.tricode, away.tricode})
    series = postseason.series.get(key)
    if series is None:
        conference = home.conference if home.conference == away.conference else None
        series = _Series((home.tricode, away.tricode), conference, _tip_off(event))
        postseason.series[key] = series
    note = competition.notes[0].headline if competition.notes else None
    match = ROUND_AND_GAME.fullmatch(note) if note else None
    series.round_label = series.round_label or (match["round"] if match else None)
    series.played = series.played or played
    if played and competition.series is not None:
        series.summary = _series_summary(competition.series) or series.summary


def _play_in_game(event: Event, competition: Competition, played: bool) -> PlayInGame:
    """The game with its places from ESPN's note, and scores once it is final."""
    note = competition.notes[0].headline if competition.notes else None
    match = PLAY_IN_NOTE.fullmatch(note) if note else None
    status = None if event.status is None else event.status.type
    final = played and status is not None and status.name == FINAL

    def side(home_away: str) -> PlayInSide:
        competitor = _competitor(event, home_away)
        score = competitor.score if final and competitor is not None else None
        return PlayInSide(tricode=_team(event, home_away).tricode, score=score)

    return PlayInGame(
        label=match["label"] if match else note or PLAY_IN_LABEL,
        tip_off=_tip_off(event),
        away=side("away"),
        home=side("home"),
    )


def _rounds(series: list[_Series]) -> list[list[_Series]]:
    """One conference's series grouped by ESPN's round, in the order the rounds began.

    A series whose games name no round is logged and left out.
    """
    by_label: dict[str, list[_Series]] = {}
    for s in sorted(series, key=lambda s: s.start):
        if s.round_label is None:
            log.warning("skipping a series with no round", extra={"teams": list(s.tricodes)})
            continue
        by_label.setdefault(s.round_label, []).append(s)
    return list(by_label.values())


def _round_name(series: _Series, conference: teams.Conference) -> str:
    """`1st Round` for ESPN's `East 1st Round`: the conference already heads its rounds."""
    assert series.round_label is not None
    return series.round_label.removeprefix(f"{conference} ")


def _playoff_seeds(
    first_round: list[_Series], standings: Fetched[EspnStandings] | None
) -> dict[str, int]:
    """Each team's playoff seed by tricode: the standings' seed, but a play-in team's is the one
    its first-round opponent's pairs with (1 v 8, 2 v 7).

    ESPN's standings may still give a play-in team its regular-season place, while seeds 1-6
    are settled when the regular season ends. Empty when the standings are unavailable.
    """
    if standings is None:
        return {}
    seeds = {
        tricode: entry.playoff_seed
        for tricode, entry in _standings(standings.data).items()
        if entry.playoff_seed is not None
    }
    for series in first_round:
        known = [seeds[t] for t in series.tricodes if t in seeds]
        if not known:
            continue
        top = min(known)
        for tricode in series.tricodes:
            if seeds.get(tricode) != top:
                seeds[tricode] = FIRST_ROUND_SEED_SUM - top
    return seeds


def _playoff_series(series: _Series, seeds: dict[str, int]) -> PlayoffSeries:
    """The better seed first; teams with no seed after those with one, otherwise as ESPN lists."""
    high, low = sorted(series.tricodes, key=lambda t: (t not in seeds, seeds.get(t, 0)))
    return PlayoffSeries(
        high=PostseasonTeam(tricode=high, seed=seeds.get(high)),
        low=PostseasonTeam(tricode=low, seed=seeds.get(low)),
        summary=series.summary,
    )


def build_leaderboard(bundle: SourceBundle) -> Leaderboard | None:
    """The top 25 qualified players by PIE, then points; `None` if a source is unavailable or
    nobody qualifies yet.

    Team games played come from NBA.com's team stats, keeping the leaderboard NBA-only. Base and
    advanced rows join on player id; a player missing a stat from either is left out. `as_of` is
    when the advanced stats, the source of PIE, were fetched.
    """
    advanced, base, team_advanced = (
        bundle.nba_player_advanced,
        bundle.nba_player_base,
        bundle.nba_team_advanced,
    )
    if advanced is None or base is None or team_advanced is None:
        return None
    # Keyed `int | None` so a row with no id looks up nothing rather than failing to type-check.
    base_by_id: dict[int | None, PlayerBase] = {
        row.player_id: row for row in base.data if row.player_id is not None
    }
    team_games = {row.team_id: row.gp for row in team_advanced.data if row.team_id is not None}
    players = [
        player
        for row in advanced.data
        if (player := _leader_row(row, base_by_id.get(row.player_id), team_games)) is not None
    ]
    if not players:
        return None
    top = sorted(players, key=lambda p: (-p.pie, -p.pts))[:LEADERBOARD_SIZE]
    return Leaderboard(
        rows=[p.model_copy(update={"rank": rank}) for rank, p in enumerate(top, start=1)],
        as_of=advanced.fetched_at,
    )


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


def _games_on(scoreboard: Fetched[EspnScoreboard] | None, day: date) -> list[Event]:
    """The scoreboard's counted games that tip off on `day` (Eastern), in ESPN's order.

    Empty when the scoreboard is unavailable. A stale snapshot can be an earlier day's
    scoreboard, so other days' games are dropped, as are games with no tip-off.
    """
    if scoreboard is None:
        return []
    return [
        event
        for event in scoreboard.data.events
        if is_counted(event)
        and event.date is not None
        and event.date.astimezone(EASTERN).date() == day
    ]


def _tip_off(event: Event) -> datetime:
    assert event.date is not None
    return event.date


def _season_type(event: Event) -> int | None:
    return None if event.season is None else event.season.type


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
        series=_series(event, competition, nba_game),
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
    if _season_type(event) == REGULAR_SEASON and (
        CUP_LABEL not in label or CUP_GROUP_LABEL in label
    ):
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
    competition = _competition(event)
    return Result(
        status=result_status,
        overtimes=max(period - REGULATION_PERIODS, 0),
        label=_label(event, competition, nba_game),
        away=_result_side(event, "away", played),
        home=_result_side(event, "home", played),
        series=_series(event, competition, nba_game),
    )


def _series(event: Event, competition: Competition, nba_game: NbaGame | None) -> SeriesState | None:
    """A playoff game's series state; `None` for any other game, or if neither source has any.

    Each part comes from ESPN, else NBA.com: the round and game number from ESPN's note, else
    `gameLabel` and `seriesGameNumber`; the summary from ESPN's series wins, else `seriesText`.
    Only season type 3 is read, since ESPN also hangs the series on a regular-season meeting of
    the same teams.
    """
    if _season_type(event) != POSTSEASON:
        return None
    note = competition.notes[0].headline if competition.notes else None
    espn = ROUND_AND_GAME.fullmatch(note) if note else None
    round_label, game = (espn["round"], espn["game"]) if espn else (None, None)
    summary = None if competition.series is None else _series_summary(competition.series)
    if nba_game is not None:
        number = GAME_NUMBER.fullmatch(nba_game.series_game_number or "")
        round_label = round_label or nba_game.game_label
        game = game or (number["game"] if number else None)
        summary = summary or nba_game.series_text
    if round_label is None and game is None and summary is None:
        return None
    return SeriesState(
        round_label=round_label,
        game_number=None if game is None else int(game),
        summary=summary,
    )


def _series_summary(series: Series) -> str | None:
    """`BOS leads 3-1`, `Series tied 2-2`, or `NYK wins 4-1`, worded as NBA.com words its
    `seriesText` and in tricodes; `None` before game 1, or if a team or its wins is missing."""
    wins: list[tuple[int, str]] = []
    for competitor in series.competitors:
        if competitor.id is None or competitor.wins is None:
            return None
        try:
            wins.append((competitor.wins, teams.by_espn_id(competitor.id).tricode))
        except teams.UnknownTeam:
            log.warning("skipping series with an unknown team", extra={"id": competitor.id})
            return None
    if len(wins) != 2:
        return None
    (high, leader), (low, _) = sorted(wins, reverse=True)
    if high == 0:
        return None
    if high == low:
        return f"Series tied {high}-{low}"
    return f"{leader} {'wins' if series.completed else 'leads'} {high}-{low}"


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


def _standings_row(tricode: str, entry: Entry) -> StandingsRow | None:
    """The entry as a row; `None` if ESPN left out its seed or record."""
    seed, wins, losses = entry.playoff_seed, entry.wins, entry.losses
    if seed is None or wins is None or losses is None:
        return None
    return StandingsRow(
        seed=seed,
        tricode=tricode,
        wins=wins,
        losses=losses,
        pct=_pct(entry.win_percent, wins, losses),
        gb=LEADER_GB if seed == 1 else _games_behind(entry.games_behind),
        last_ten=entry.last_ten_games,
        streak=entry.streak,
        clinch=_clinch(tricode, entry.clincher),
    )


def _pct(win_percent: float | None, wins: int, losses: int) -> str:
    """`.732`, or `1.000`; computed from the record if ESPN left the percentage out."""
    if win_percent is None:
        win_percent = wins / (wins + losses) if wins + losses else 0.0
    return f"{win_percent:.3f}".removeprefix("0")


def _games_behind(games_behind: float | None) -> str:
    """`4`, or `4.5` for a half game; blank if ESPN left it out, since a dash would read as
    tied for first."""
    if games_behind is None:
        return ""
    return f"{games_behind:g}"


def _clinch(tricode: str, code: str | None) -> Clinch | None:
    if not code:
        return None
    clinch = CLINCH_CODES.get(code)
    if clinch is None:
        log.warning("unknown clinch code", extra={"team": tricode, "code": code})
    return clinch


def _leader_row(
    advanced: PlayerAdvanced, base: PlayerBase | None, team_games: dict[int, int | None]
) -> LeaderRow | None:
    """The player's line, unranked, if they qualify; `None` if not, or if a stat is missing."""
    a = advanced
    if base is None or a.player_name is None or a.team_id is None:
        return None
    if a.gp is None or a.min is None or a.pie is None or a.ts_pct is None:
        return None
    if base.pts is None or base.reb is None or base.ast is None:
        return None
    games = team_games.get(a.team_id)
    if games is None or not is_qualified(a.gp, a.min, games):
        return None
    try:
        tricode = teams.by_nba_id(a.team_id).tricode
    except teams.UnknownTeam:
        log.warning("skipping leaderboard row with an unknown team", extra={"id": a.team_id})
        return None
    return LeaderRow(
        rank=0,
        name=a.player_name,
        tricode=tricode,
        pts=base.pts,
        reb=base.reb,
        ast=base.ast,
        ts_pct=a.ts_pct,
        pie=a.pie,
    )


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
