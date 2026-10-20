import pytest

from nba_today.digest.qualify import is_qualified


@pytest.mark.parametrize(
    ("gp", "team_games", "expected"),
    [
        # 0.70 * 10 is 7.000000000000001 in floating point; seven games is still exactly 70%.
        (7, 10, True),
        (6, 10, False),
        # ceil(0.70 * 82) is 58.
        (58, 82, True),
        (57, 82, False),
    ],
)
def test_qualifies_from_seventy_percent_of_team_games(
    gp: int, team_games: int, expected: bool
) -> None:
    assert is_qualified(gp, 20.0, team_games) is expected


def test_twenty_minutes_per_game_qualifies() -> None:
    assert is_qualified(10, 20.0, 10)


def test_under_twenty_minutes_per_game_does_not_qualify() -> None:
    assert not is_qualified(10, 19.9, 10)


def test_nobody_qualifies_before_the_team_has_played() -> None:
    assert not is_qualified(0, 36.0, 0)
