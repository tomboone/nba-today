"""The digest → subject, HTML, and plain text (spec §6)."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from nba_today.digest.build import build_digest, build_postseason
from nba_today.digest.models import (
    Clinch,
    Digest,
    InjuryReport,
    Leader,
    PlayInGame,
    PlayInSide,
    PlayoffSeries,
    PostseasonPicture,
    PostseasonTeam,
    Result,
    ResultSide,
    SeriesState,
)
from nba_today.render.render import (
    ampm,
    clinch_mark,
    game_label,
    overtime,
    percent,
    play_in_line,
    record,
    render,
    score_note,
    series_line,
    signed,
    stamp,
    status,
)
from nba_today.sources.fetched import Fetched
from nba_today.sources.models.espn_scoreboard import EspnScoreboard
from tests.bundles import (
    EMPTY_SCOREBOARD,
    FULL_DAY,
    OPENING_DAY,
    POSTSEASON_DAY,
    SNAPSHOT_AT,
    fresh,
    full_day_bundle,
    opening_day_bundle,
    postseason_bundle,
)


class HTMLSnapshot(SingleFileSnapshotExtension):
    """One browsable `.html` file per snapshot, so a reviewer can open the rendered email."""

    file_extension = "html"
    _write_mode = WriteMode.TEXT


class TextSnapshot(SingleFileSnapshotExtension):
    file_extension = "txt"
    _write_mode = WriteMode.TEXT


def _full() -> Digest:
    return build_digest(FULL_DAY, full_day_bundle())


def _scores_only() -> Digest:
    no_games = fresh(EspnScoreboard.model_validate(EMPTY_SCOREBOARD))
    return build_digest(FULL_DAY, replace(full_day_bundle(), espn_scoreboard_today=no_games))


def _stale() -> Digest:
    bundle = full_day_bundle()
    assert bundle.espn_injuries is not None
    injuries = Fetched(bundle.espn_injuries.data, SNAPSHOT_AT, stale=True)
    return build_digest(FULL_DAY, replace(bundle, espn_injuries=injuries))


def _enrichment_unavailable() -> Digest:
    """NBA.com down with no snapshots: ESPN broadcasts, no ratings, no leaderboard."""
    bundle = replace(
        full_day_bundle(),
        nba_schedule=None,
        nba_team_advanced=None,
        nba_player_advanced=None,
        nba_player_base=None,
    )
    return build_digest(FULL_DAY, bundle)


def _postseason() -> Digest:
    return build_digest(POSTSEASON_DAY, postseason_bundle())


DIGESTS = {
    "full": _full,
    "scores_only": _scores_only,
    "stale": _stale,
    "enrichment_unavailable": _enrichment_unavailable,
    "postseason": _postseason,
}


@pytest.mark.parametrize("kind", DIGESTS)
def test_html_matches_the_snapshot(kind: str, snapshot: SnapshotAssertion) -> None:
    # `render` raises `ValueError` when `mjml` cannot compile the source, so a match also means a
    # clean compile. The compiler rejects malformed markup but tolerates unknown tags.
    assert render(DIGESTS[kind]()).html == snapshot.use_extension(HTMLSnapshot)


@pytest.mark.parametrize("kind", DIGESTS)
def test_text_matches_the_snapshot(kind: str, snapshot: SnapshotAssertion) -> None:
    assert render(DIGESTS[kind]()).text == snapshot.use_extension(TextSnapshot)


def test_a_full_slate_is_under_the_size_budget() -> None:
    # The fixture night has five games; a full NBA slate has fifteen.
    digest = _full()
    slate = digest.model_copy(
        update={"matchups": digest.matchups * 3, "results": digest.results * 3}
    )

    assert len(render(slate).html.encode()) < 150_000


@pytest.mark.parametrize(
    ("kind", "day", "expected"),
    [
        ("full", date(2026, 10, 21), "NBA Today — Oct 21"),
        ("scores_only", date(2026, 2, 13), "NBA Scores — Feb 13"),
    ],
)
def test_subject_names_the_kind_and_date(kind: str, day: date, expected: str) -> None:
    digest = DIGESTS[kind]().model_copy(update={"date": day})

    assert render(digest).subject == expected


def test_scores_only_omits_matchups_but_keeps_results() -> None:
    rendered = render(_scores_only())

    assert "TODAY'S MATCHUPS" not in rendered.text
    assert "TD Garden" not in rendered.html
    assert "LAST NIGHT'S RESULTS" in rendered.text


def test_stale_source_shows_a_banner_naming_it_and_its_snapshot_time() -> None:
    rendered = render(_stale())
    banner = "ESPN injuries could not be fetched; showing its snapshot from Oct 19, 8:00 AM ET."

    assert banner in rendered.html
    assert banner in rendered.text


def test_fresh_digest_has_no_banner() -> None:
    rendered = render(_full())

    assert "could not be fetched" not in rendered.html
    assert "Not available today" not in rendered.html


def test_stale_player_stats_collapse_into_one_line_with_the_oldest_time() -> None:
    older = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
    digest = _full().model_copy(
        update={"stale": {"nba_player_advanced": SNAPSHOT_AT, "nba_player_base": older}}
    )

    text = render(digest).text

    assert text.count("NBA.com player stats could not be fetched") == 1
    assert "snapshot from Oct 19, 12:00 AM ET" in text


def test_unavailable_enrichment_is_named_and_its_fields_are_dropped() -> None:
    rendered = render(_enrichment_unavailable())

    unavailable = (
        "Not available today: NBA.com schedule, NBA.com team ratings, NBA.com player stats."
    )
    assert unavailable in rendered.text
    assert "ORtg" not in rendered.text
    assert "PIE LEADERS" not in rendered.text
    assert "No qualified players yet" not in rendered.text


def test_no_qualified_players_says_so() -> None:
    rendered = render(build_digest(OPENING_DAY, opening_day_bundle()))

    assert "No qualified players yet" in rendered.html
    assert "No qualified players yet" in rendered.text
    assert "LAST NIGHT'S RESULTS" not in rendered.text


def test_broadcasts_list_national_then_home_then_away() -> None:
    digest = _full()
    first = digest.matchups[0]
    tv = first.tv.model_copy(update={"national": ["ESPN"]})
    digest = digest.model_copy(update={"matchups": [first.model_copy(update={"tv": tv})]})

    rendered = render(digest)

    line = "TV: National: ESPN · BOS: NBC Sports BO · LAC: FanDuel SN SoCal"
    assert line in rendered.html
    assert line in rendered.text


def test_a_matchup_label_and_every_injury_status_show() -> None:
    digest = _full()
    first = digest.matchups[0]
    injuries = InjuryReport(out=["A One"], day_to_day=["B Two"], other=[("C Three", "Suspension")])
    away = first.away.model_copy(update={"injuries": injuries})
    labelled = first.model_copy(update={"label": "NBA Cup Semifinal", "away": away})
    digest = digest.model_copy(update={"matchups": [labelled]})

    rendered = render(digest)

    for text in ("NBA Cup Semifinal", "Out: A One", "DTD: B Two", "Suspension: C Three"):
        assert text in rendered.text
    assert "NBA Cup Semifinal" in rendered.html


def test_postseason_games_show_their_series_in_place_of_the_label() -> None:
    rendered = render(_postseason())

    # The matchup's state is the sweep: its scoreboard was recorded after the game.
    for line in (
        "West 1st Round · Game 4 · OKC wins 4-0",
        "West 1st Round · Game 4 · SAS leads 3-1",
    ):
        assert line in rendered.text
        assert line in rendered.html
    assert "1st Round - Game 4" not in rendered.text


def _picture(day: date) -> PostseasonPicture:
    picture = build_postseason(day, postseason_bundle())
    assert picture is not None
    return picture


def test_a_postseason_digest_shows_the_picture_in_place_of_the_standings() -> None:
    rendered = render(_postseason())

    for line in ("(1) DET vs (8) ORL · ORL leads 2-1", "(4) LAL vs (5) HOU · LAL leads 3-1"):
        assert line in rendered.text
        assert line in rendered.html
    assert "POSTSEASON" in rendered.text
    assert "STANDINGS" not in rendered.text
    assert "Standings" not in rendered.html


def test_the_play_in_lists_scores_and_unplayed_games() -> None:
    digest = _postseason().model_copy(update={"postseason": _picture(date(2026, 4, 15))})

    rendered = render(digest)

    for line in ("9th Place vs 10th Place: MIA 126, CHA 127", "7th Place vs 8th Place: ORL @ PHI"):
        assert line in rendered.text
        assert line in rendered.html
    assert "(1) DET vs (8) ORL" not in rendered.text


def test_the_finals_head_the_picture() -> None:
    digest = _postseason().model_copy(update={"postseason": _picture(date(2026, 6, 14))})

    text = render(digest).text

    assert "NBA Finals\n  (2) SAS vs (3) NYK · NYK wins 4-1" in text
    assert text.index("NBA Finals") < text.index("East\n  Finals")


def test_a_full_postseason_picture_is_under_the_size_budget() -> None:
    # Every round so far, the morning after the Finals, beside a first-round night of eight games.
    digest = _postseason()
    full = digest.model_copy(
        update={
            "matchups": digest.matchups * 3,
            "results": digest.results * 3,
            "postseason": _picture(date(2026, 6, 14)),
        }
    )

    assert len(render(full).html.encode()) < 150_000


def test_series_line() -> None:
    def team(tricode: str, seed: int | None) -> PostseasonTeam:
        return PostseasonTeam(tricode=tricode, seed=seed)

    started = PlayoffSeries(high=team("DET", 1), low=team("ORL", 8), summary="DET wins 4-3")
    unseeded = PlayoffSeries(high=team("SAS", None), low=team("NYK", None), summary=None)

    assert series_line(started) == "(1) DET vs (8) ORL · DET wins 4-3"
    assert series_line(unseeded) == "SAS vs NYK"


def test_play_in_line() -> None:
    game = PlayInGame(
        label="8th Seed Game",
        tip_off=datetime(2026, 4, 17, 23, 30, tzinfo=UTC),
        away=PlayInSide(tricode="CHA", score=90),
        home=PlayInSide(tricode="ORL", score=121),
    )
    unplayed = game.model_copy(
        update={
            "away": PlayInSide(tricode="CHA", score=None),
            "home": PlayInSide(tricode="ORL", score=None),
        }
    )

    assert play_in_line(game) == "8th Seed Game: CHA 90, ORL 121"
    assert play_in_line(unplayed) == "8th Seed Game: CHA @ ORL"


def test_a_postponed_result_shows_its_status_and_no_score() -> None:
    side = ResultSide(tricode="LAC", score=None)
    postponed = Result(
        status="postponed",
        overtimes=0,
        label=None,
        away=side,
        home=side.model_copy(update={"tricode": "BOS"}),
    )
    digest = _full().model_copy(update={"results": [postponed]})

    rendered = render(digest)

    assert "LAC @ BOS: Postponed" in rendered.text
    assert "Postponed" in rendered.html


def test_a_result_with_partial_leaders_lists_only_those() -> None:
    digest = _full()
    first = digest.results[0]
    assert first.away.leaders is not None
    leaders = first.away.leaders.model_copy(
        update={"points": Leader(name="Solo Scorer", value=40), "rebounds": None, "assists": None}
    )
    away = first.away.model_copy(update={"leaders": leaders})
    home = first.home.model_copy(update={"leaders": None})
    digest = digest.model_copy(
        update={"results": [first.model_copy(update={"away": away, "home": home})]}
    )

    text = render(digest).text

    assert "  LAC: Solo Scorer 40 pts\n" in text
    assert "  BOS:" not in text


def test_standings_draw_lines_after_seeds_6_and_10_and_mark_clinches() -> None:
    rendered = render(_full())

    assert rendered.text.count("-" * 43) == 4
    assert "  1 DET z" in rendered.text
    assert "z best record · y division · x playoffs · pi play-in · e eliminated" in rendered.html


def test_html_escapes_source_text() -> None:
    digest = _full()
    first = digest.matchups[0]
    renamed = first.model_copy(update={"arena": "Garden & <Arena>"})
    digest = digest.model_copy(update={"matchups": [renamed, *digest.matchups[1:]]})

    rendered = render(digest)

    assert "Garden &amp; &lt;Arena&gt;" in rendered.html
    assert "Garden & <Arena>" in rendered.text


def test_ampm_reads_in_eastern() -> None:
    assert ampm(datetime(2025, 11, 18, 0, 30, tzinfo=UTC)) == "7:30 PM"
    assert ampm(datetime(2025, 11, 17, 17, 0, tzinfo=UTC)) == "12:00 PM"


def test_stamp_reads_in_eastern() -> None:
    assert stamp(datetime(2026, 10, 19, 12, 0, tzinfo=UTC)) == "Oct 19, 8:00 AM ET"


@pytest.mark.parametrize(
    ("value", "expected"), [(8.34, "+8.3"), (-9.7, "-9.7"), (0.04, "0.0"), (-0.04, "0.0")]
)
def test_signed(value: float, expected: str) -> None:
    assert signed(value) == expected


def test_percent_is_one_decimal() -> None:
    assert (percent(0.626), percent(0.2094)) == ("62.6", "20.9")


def test_record_is_empty_when_unknown() -> None:
    assert (record(4, 9), record(None, 9), record(4, None)) == ("4-9", "", "")


@pytest.mark.parametrize(("overtimes", "expected"), [(0, ""), (1, "OT"), (2, "2OT"), (3, "3OT")])
def test_overtime(overtimes: int, expected: str) -> None:
    assert overtime(overtimes) == expected


@pytest.mark.parametrize(
    ("result_status", "expected"),
    [("postponed", "Postponed"), ("Suspended - Weather", "Suspended - Weather")],
)
def test_status(result_status: str, expected: str) -> None:
    side = ResultSide(tricode="BOS", score=None)
    assert status(Result(status=result_status, overtimes=0, label=None, away=side, home=side)) == (
        expected
    )


@pytest.mark.parametrize(
    ("result_status", "overtimes", "expected"),
    [
        ("final", 0, ""),
        ("final", 2, "2OT"),
        ("Final - Protested", 1, "OT, Final - Protested"),
    ],
)
def test_score_note(result_status: str, overtimes: int, expected: str) -> None:
    side = ResultSide(tricode="BOS", score=100)
    result = Result(status=result_status, overtimes=overtimes, label=None, away=side, home=side)

    assert score_note(result) == expected


def test_every_clinch_has_a_mark() -> None:
    assert [clinch_mark(c) for c in Clinch] == ["z", "y", "x", "pi", "e"]
    assert clinch_mark(None) == ""


@pytest.mark.parametrize(
    ("label", "series", "expected"),
    [
        (None, None, ""),
        ("NBA Cup Semifinal", None, "NBA Cup Semifinal"),
        (
            "NBA Finals - Game 5",
            SeriesState(round_label="NBA Finals", game_number=5, summary="NYK wins 4-1"),
            "NBA Finals · Game 5 · NYK wins 4-1",
        ),
        (
            None,
            SeriesState(round_label="East 1st Round", game_number=1, summary=None),
            "East 1st Round · Game 1",
        ),
        (
            None,
            SeriesState(round_label=None, game_number=None, summary="Series tied 2-2"),
            "Series tied 2-2",
        ),
    ],
)
def test_game_label(label: str | None, series: SeriesState | None, expected: str) -> None:
    side = ResultSide(tricode="BOS", score=100)
    result = Result(status="final", overtimes=0, label=label, away=side, home=side, series=series)

    assert game_label(result) == expected
