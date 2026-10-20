import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from nba_today.pipeline import DigestPipeline, build, fetch_bundle, run_send
from nba_today.settings import Settings
from nba_today.sources.snapshot import SnapshotStore
from tests.bundles import FULL_DAY
from tests.mocked_sources import (
    NBA_SOURCES,
    PAYLOADS,
    SOURCES,
    FakeNbaSession,
    mock_espn,
    seed_snapshots,
    use_nba_session,
)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr("nba_today.sources.http.time.sleep", no_sleep)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate({"recipients": ["fan@example.com"], "data_dir": tmp_path})


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeNbaSession:
    fake = FakeNbaSession()
    use_nba_session(monkeypatch, fake)
    return fake


def _no_nba_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> FakeNbaSession:
        raise AssertionError("opened an NBA.com session")

    monkeypatch.setattr("nba_today.pipeline.make_nba_session", refuse)


@respx.mock
def test_fetches_every_source_once_and_snapshots_it(
    settings: Settings, session: FakeNbaSession
) -> None:
    routes = mock_espn(respx.mock)

    digest, rendered = build(FULL_DAY, settings)

    assert all(route.call_count == 1 for route in routes.values())
    assert respx.calls.call_count == len(routes)
    assert len(session.calls) == len(NBA_SOURCES)
    assert (digest.kind, digest.stale, digest.unavailable) == ("full", {}, [])
    assert digest.matchups
    assert digest.results
    assert digest.leaderboard
    assert "NBA Today" in rendered.subject
    store = SnapshotStore(settings.data_dir)
    assert all(store.get(source) is not None for source in SOURCES)


@respx.mock
def test_logs_one_line_per_source_with_status_and_duration(
    settings: Settings, session: FakeNbaSession, caplog: pytest.LogCaptureFixture
) -> None:
    store = SnapshotStore(settings.data_dir)
    store.put("espn_standings", PAYLOADS["espn_standings"])
    routes = mock_espn(respx.mock)
    routes["espn_standings"].return_value = httpx.Response(503)
    routes["espn_injuries"].return_value = httpx.Response(503)

    with caplog.at_level(logging.INFO, logger="nba_today.pipeline"):
        fetch_bundle(FULL_DAY, settings)

    lines = [r for r in caplog.records if r.getMessage() == "source fetched"]
    assert [vars(r)["source"] for r in lines] == SOURCES
    outcomes = {vars(r)["source"]: (vars(r)["status"], r.levelname) for r in lines}
    assert outcomes == {source: ("ok", "INFO") for source in SOURCES} | {
        "espn_standings": ("stale", "WARNING"),
        "espn_injuries": ("unavailable", "WARNING"),
    }
    assert all(isinstance(vars(r)["duration_ms"], int) for r in lines)


@respx.mock
def test_enrichment_off_logs_nba_sources_unavailable_at_info(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    mock_espn(respx.mock)
    _no_nba_session(monkeypatch)
    settings = settings.model_copy(update={"nba_enrichment": False})

    with caplog.at_level(logging.INFO, logger="nba_today.pipeline"):
        fetch_bundle(FULL_DAY, settings)

    nba_lines = [
        r
        for r in caplog.records
        if r.getMessage() == "source fetched" and vars(r)["source"] in NBA_SOURCES
    ]
    assert [(vars(r)["status"], r.levelname) for r in nba_lines] == [("unavailable", "INFO")] * 4


@respx.mock
def test_an_espn_source_failing_never_aborts_the_others(
    settings: Settings, session: FakeNbaSession
) -> None:
    routes = mock_espn(respx.mock)
    routes["espn_standings"].return_value = httpx.Response(503)

    bundle = fetch_bundle(FULL_DAY, settings)

    assert bundle.espn_standings is None
    assert routes["espn_injuries"].call_count == 1
    assert bundle.espn_athletes is not None
    assert bundle.nba_player_base is not None


@respx.mock
def test_every_nba_fetch_raising_still_sends_a_digest_saying_so(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    mock_espn(respx.mock)
    session = FakeNbaSession(fail=True)
    use_nba_session(monkeypatch, session)
    settings = settings.model_copy(update={"dry_run": True})

    with caplog.at_level(logging.INFO, logger="nba_today.pipeline"):
        receipt = run_send(FULL_DAY, settings)

    assert len(session.calls) == len(NBA_SOURCES)
    [sent] = [r for r in caplog.records if r.getMessage() == "digest sent"]
    assert vars(sent)["unavailable"] == NBA_SOURCES
    assert vars(sent)["kind"] == "full"
    assert receipt.transport == "outbox"
    assert (settings.data_dir / "outbox" / "2025-11-17.html").exists()


@respx.mock
def test_enrichment_off_never_opens_an_nba_session(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_espn(respx.mock)
    _no_nba_session(monkeypatch)
    settings = settings.model_copy(update={"nba_enrichment": False})

    digest, _ = build(FULL_DAY, settings)

    assert digest.unavailable == NBA_SOURCES


def test_offline_reads_snapshots_and_makes_no_network_calls(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_snapshots(SnapshotStore(settings.data_dir))
    _no_nba_session(monkeypatch)

    with respx.mock(assert_all_called=False) as router:
        anything = router.route().mock(return_value=httpx.Response(500))
        digest, _ = build(FULL_DAY, settings, offline=True)

    assert anything.call_count == 0
    assert digest.kind == "full"
    assert digest.unavailable == []
    assert set(digest.stale) == set(SOURCES)


def test_offline_without_snapshots_builds_a_digest_saying_so(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_nba_session(monkeypatch)

    with respx.mock(assert_all_called=False) as router:
        anything = router.route().mock(return_value=httpx.Response(500))
        digest, _ = build(FULL_DAY, settings, offline=True)

    assert anything.call_count == 0
    assert digest.kind == "scores_only"
    assert digest.unavailable == SOURCES


@respx.mock
def test_requests_yesterday_and_the_digest_season(
    settings: Settings, session: FakeNbaSession
) -> None:
    routes = mock_espn(respx.mock)

    fetch_bundle(FULL_DAY, settings)

    assert routes["espn_scoreboard_yesterday"].calls.last.request.url.params["dates"] == "20251116"
    assert routes["espn_standings"].calls.last.request.url.params["season"] == "2026"
    stats = [params for _, params in session.calls if params is not None]
    assert {params["Season"] for params in stats} == {"2025-26"}


EASTERN = ZoneInfo("America/New_York")
SCOREBOARDS = ("espn_scoreboard_today", "espn_scoreboard_yesterday")


def _seed_scoreboards(settings: Settings, fetched_at: datetime) -> None:
    """Today's and yesterday's scoreboard snapshots, recorded as taken at `fetched_at`."""
    for source in SCOREBOARDS:
        stored = {"fetched_at": fetched_at.isoformat(), "payload": PAYLOADS[source]}
        path = settings.data_dir / "snapshots" / f"{source}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored))


def _scoreboards_down(router: respx.MockRouter) -> None:
    routes = mock_espn(router)
    for source in SCOREBOARDS:
        routes[source].return_value = httpx.Response(503)


def test_scoreboards_fetches_today_and_yesterday(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_espn(router)
        today, yesterday = DigestPipeline(settings).scoreboards(FULL_DAY)

    assert today is not None
    assert today.events
    assert yesterday is not None
    assert yesterday.events
    assert routes["espn_scoreboard_yesterday"].calls.last.request.url.params["dates"] == "20251116"
    # Planning needs the scoreboards alone.
    assert routes["espn_standings"].call_count == 0


def test_scoreboards_uses_a_snapshot_taken_today(settings: Settings) -> None:
    _seed_scoreboards(settings, datetime(2025, 11, 17, 3, tzinfo=EASTERN))

    with respx.mock(assert_all_called=False) as router:
        _scoreboards_down(router)
        today, yesterday = DigestPipeline(settings).scoreboards(FULL_DAY)

    assert today is not None
    assert yesterday is not None


def test_scoreboards_rejects_a_snapshot_from_another_day(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # 23:59 Eastern on the 16th is already the 17th in UTC; the local date decides.
    _seed_scoreboards(settings, datetime(2025, 11, 16, 23, 59, tzinfo=EASTERN))

    with (
        respx.mock(assert_all_called=False) as router,
        caplog.at_level(logging.ERROR, logger="nba_today.pipeline"),
    ):
        _scoreboards_down(router)
        scoreboards = DigestPipeline(settings).scoreboards(FULL_DAY)

    assert scoreboards == (None, None)
    assert caplog.messages.count("scoreboard snapshot is from another day") == 2


def test_scoreboards_without_a_snapshot_is_unavailable(settings: Settings) -> None:
    with respx.mock(assert_all_called=False) as router:
        _scoreboards_down(router)
        scoreboards = DigestPipeline(settings).scoreboards(FULL_DAY)

    assert scoreboards == (None, None)
