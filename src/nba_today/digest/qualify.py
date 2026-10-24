"""The qualified-player rule, shared by key players and the PIE leaderboard (spec §6).

A player qualifies with at least 70% of their team's games and at least 20 minutes per game. The
caller supplies the team's games played: key players count them from ESPN's standings, the
leaderboard from NBA.com's team stats, so each stays within one source.
"""

MIN_GAMES_SHARE_PERCENT = 70
MIN_MINUTES_PER_GAME = 20.0


def is_qualified(gp: int, mpg: float, team_games: int) -> bool:
    """`gp >= ceil(0.70 * team_games)` and `mpg >= 20.0`; with no team games, nobody qualifies.

    The share is compared in integers: `0.70 * 10` is `7.000000000000001` in floating point,
    which would turn away a player with exactly seven games of ten.
    """
    if team_games <= 0:
        return False
    return gp * 100 >= MIN_GAMES_SHARE_PERCENT * team_games and mpg >= MIN_MINUTES_PER_GAME
