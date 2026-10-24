from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nba_today.sources.fetched import SourceUnavailable, fetch_or_snapshot
from nba_today.sources.snapshot import Payload, SnapshotStore


def _events(payload: Payload) -> list[str]:
    return [e["id"] for e in payload["events"]]


def _fails() -> Payload:
    raise httpx.ConnectError("refused")


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path)


def test_success_parses_stores_and_is_fresh(store: SnapshotStore) -> None:
    before = datetime.now(UTC)

    result = fetch_or_snapshot(
        "espn_scoreboard", lambda: {"events": [{"id": "401810007"}]}, _events, store
    )

    assert result.data == ["401810007"]
    assert result.stale is False
    assert before <= result.fetched_at <= datetime.now(UTC)
    assert store.get("espn_scoreboard") == ({"events": [{"id": "401810007"}]}, result.fetched_at)


def test_failure_with_snapshot_is_stale_with_snapshot_time(store: SnapshotStore) -> None:
    snapshot_at = store.put("espn_scoreboard", {"events": [{"id": "401810003"}]})

    result = fetch_or_snapshot("espn_scoreboard", _fails, _events, store)

    assert result.data == ["401810003"]
    assert result.stale is True
    assert result.fetched_at == snapshot_at


def test_failure_without_snapshot_raises_source_unavailable(store: SnapshotStore) -> None:
    with pytest.raises(SourceUnavailable) as excinfo:
        fetch_or_snapshot("espn_scoreboard", _fails, _events, store)

    assert excinfo.value.source == "espn_scoreboard"


def test_unparseable_fresh_payload_falls_back_and_keeps_snapshot(store: SnapshotStore) -> None:
    snapshot_at = store.put("espn_scoreboard", {"events": [{"id": "401810003"}]})

    result = fetch_or_snapshot("espn_scoreboard", lambda: {"unexpected": True}, _events, store)

    assert result.data == ["401810003"]
    assert result.stale is True
    assert store.get("espn_scoreboard") == ({"events": [{"id": "401810003"}]}, snapshot_at)


def test_unparseable_snapshot_raises_source_unavailable(store: SnapshotStore) -> None:
    store.put("espn_scoreboard", {"unexpected": True})

    with pytest.raises(SourceUnavailable):
        fetch_or_snapshot("espn_scoreboard", _fails, _events, store)


def test_snapshot_write_failure_still_returns_fresh_data(
    store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(source: str, payload: Payload) -> datetime:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "put", full_disk)

    result = fetch_or_snapshot(
        "espn_scoreboard", lambda: {"events": [{"id": "401810007"}]}, _events, store
    )

    assert result.data == ["401810007"]
    assert result.stale is False


def test_no_fetch_fn_reads_the_snapshot_alone(store: SnapshotStore) -> None:
    snapshot_at = store.put("espn_scoreboard", {"events": [{"id": "401810003"}]})

    result = fetch_or_snapshot("espn_scoreboard", None, _events, store)

    assert (result.data, result.fetched_at, result.stale) == (["401810003"], snapshot_at, True)


def test_no_fetch_fn_without_snapshot_raises_source_unavailable(store: SnapshotStore) -> None:
    with pytest.raises(SourceUnavailable):
        fetch_or_snapshot("espn_scoreboard", None, _events, store)
