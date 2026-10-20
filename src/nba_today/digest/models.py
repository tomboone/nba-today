"""The digest: everything the email shows (spec §6).

Fields a later builder fills (results, standings, leaderboard) default to empty or `None`, so
each section can be built and tested on its own.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from nba_today.teams import Conference


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class InjuryReport(_Model):
    out: list[str] = []
    day_to_day: list[str] = []
    # (name, status) for any status other than Out or Day-To-Day, rendered verbatim.
    other: list[tuple[str, str]] = []


class TeamRatings(_Model):
    off_rtg: float
    def_rtg: float
    net_rtg: float
    pace: float


class KeyPlayer(_Model):
    name: str
    ppg: float
    rpg: float
    apg: float


class Broadcasts(_Model):
    """TV and streaming merged, deduped, in source order."""

    national: list[str] = []
    home: list[str] = []
    away: list[str] = []


class MatchupSide(_Model):
    tricode: str
    # `None` only when neither the scoreboard nor the standings has the team's record.
    wins: int | None
    losses: int | None
    last_ten: str | None = None
    streak: str | None = None
    injuries: InjuryReport = InjuryReport()
    ratings: TeamRatings | None = None
    key_player: KeyPlayer | None = None


class Matchup(_Model):
    tip_off: datetime
    # A Cup-knockout, play-in, or postseason label; `None` for any other game.
    label: str | None
    # `None` where ESPN left the venue out.
    arena: str | None
    city: str | None
    away: MatchupSide
    home: MatchupSide
    tv: Broadcasts


class Leader(_Model):
    name: str
    value: int


class GameLeaders(_Model):
    points: Leader | None = None
    rebounds: Leader | None = None
    assists: Leader | None = None


class ResultSide(_Model):
    tricode: str
    # `None` for a game with no score (postponed).
    score: int | None
    leaders: GameLeaders | None = None


class Result(_Model):
    # "final", "postponed", or ESPN's status detail verbatim for anything else.
    status: str
    # 0 for regulation.
    overtimes: int
    label: str | None
    away: ResultSide
    home: ResultSide


class Clinch(StrEnum):
    BEST_RECORD = "best_record"
    DIVISION = "division"
    PLAYOFF = "playoff"
    PLAY_IN = "play_in"
    ELIMINATED = "eliminated"


class StandingsRow(_Model):
    seed: int
    tricode: str
    wins: int
    losses: int
    pct: str  # ".732"
    gb: str  # "-" for seed 1
    last_ten: str | None
    streak: str | None
    clinch: Clinch | None = None


class ConferenceStandings(_Model):
    conference: Conference
    rows: list[StandingsRow]  # 15, by seed


class LeaderRow(_Model):
    rank: int
    name: str
    tricode: str
    pts: float
    reb: float
    ast: float
    ts_pct: float
    pie: float


class Leaderboard(_Model):
    rows: list[LeaderRow]  # at most 25, by PIE desc
    as_of: datetime


type DigestKind = Literal["full", "scores_only"]
type Phase = Literal["regular", "postseason"]


class Digest(_Model):
    date: date  # Eastern
    kind: DigestKind
    # The postseason swaps the standings for the postseason picture (spec §13).
    phase: Phase = "regular"
    # Snapshot name → snapshot time, for each source that fell back to its snapshot.
    stale: dict[str, datetime] = {}
    # Snapshot names with no snapshot either; their sections or fields are omitted.
    unavailable: list[str] = []
    # Empty on a scores-only digest; sorted by tip-off, then home tricode.
    matchups: list[Matchup] = []
    # Yesterday's counted games, in tip-off order.
    results: list[Result] = []
    # East, West; `None` when unavailable.
    standings: list[ConferenceStandings] | None = None
    # `None` when unavailable or no player is qualified yet.
    leaderboard: Leaderboard | None = None
