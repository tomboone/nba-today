from collections import Counter

import pytest

from nba_today.teams import (
    TEAMS,
    UnknownTeam,
    by_espn_abbr,
    by_espn_id,
    by_nba_id,
    by_tricode,
)


def test_table_has_30_teams_unique_on_every_key() -> None:
    assert len(TEAMS) == 30
    assert len({t.espn_id for t in TEAMS}) == 30
    assert len({t.espn_abbr for t in TEAMS}) == 30
    assert len({t.nba_id for t in TEAMS}) == 30
    assert len({t.tricode for t in TEAMS}) == 30


def test_15_teams_per_conference() -> None:
    assert Counter(t.conference for t in TEAMS) == {"East": 15, "West": 15}


def test_espn_abbr_resolves_to_nba_tricode() -> None:
    assert by_espn_abbr("NY").tricode == "NYK"
    assert by_espn_abbr("UTAH").tricode == "UTA"


def test_nba_id_resolves_to_tricode() -> None:
    assert by_nba_id(1610612738).tricode == "BOS"


def test_every_lookup_finds_the_same_row() -> None:
    for team in TEAMS:
        assert by_espn_id(team.espn_id) is team
        assert by_espn_abbr(team.espn_abbr) is team
        assert by_nba_id(team.nba_id) is team
        assert by_tricode(team.tricode) is team


def test_unknown_keys_raise() -> None:
    # 50015 is the London Lions, a preseason opponent in NBA.com's schedule.
    with pytest.raises(UnknownTeam):
        by_nba_id(50015)
    with pytest.raises(UnknownTeam):
        by_espn_id(99)
    with pytest.raises(UnknownTeam):
        by_espn_abbr("NYK")
    with pytest.raises(UnknownTeam):
        by_tricode("NY")
