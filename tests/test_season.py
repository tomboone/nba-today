from datetime import date

import pytest

from nba_today.season import espn_season, season_label, season_start_year


@pytest.mark.parametrize(
    ("d", "start_year", "label", "espn_year"),
    [
        (date(2026, 10, 20), 2026, "2026-27", 2027),
        (date(2027, 5, 1), 2026, "2026-27", 2027),
        (date(2027, 7, 31), 2026, "2026-27", 2027),
        (date(2027, 8, 1), 2027, "2027-28", 2028),
        (date(2099, 10, 1), 2099, "2099-00", 2100),
    ],
)
def test_season_rolls_over_in_august(d: date, start_year: int, label: str, espn_year: int) -> None:
    assert season_start_year(d) == start_year
    assert season_label(d) == label
    assert espn_season(d) == espn_year
