"""Season naming. A season is named by the year it starts; it rolls over on August 1.

NBA.com labels a season `"2026-27"`; ESPN's `season` parameter is its end year, `2027`. Pass an
Eastern-time date: that is the calendar every "today" in nba-today is measured on.
"""

from datetime import date

ROLLOVER_MONTH = 8


def season_start_year(d: date) -> int:
    return d.year if d.month >= ROLLOVER_MONTH else d.year - 1


def season_label(d: date) -> str:
    start = season_start_year(d)
    return f"{start}-{(start + 1) % 100:02d}"


def espn_season(d: date) -> int:
    return season_start_year(d) + 1
