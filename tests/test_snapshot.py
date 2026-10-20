from datetime import UTC
from pathlib import Path

from nba_today.sources.snapshot import SnapshotStore


def test_round_trip(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    payload = {"events": [{"id": "401810001"}]}

    fetched_at = store.put("espn_scoreboard", payload)

    assert fetched_at.tzinfo is UTC
    assert store.get("espn_scoreboard") == (payload, fetched_at)
    assert (tmp_path / "snapshots" / "espn_scoreboard.json").is_file()


def test_missing_snapshot_is_none(tmp_path: Path) -> None:
    assert SnapshotStore(tmp_path).get("espn_scoreboard") is None


def test_put_replaces_previous_and_leaves_no_temp_files(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)

    store.put("nba_schedule", {"data": [1]})
    second = store.put("nba_schedule", {"data": [2]})

    assert store.get("nba_schedule") == ({"data": [2]}, second)
    assert [p.name for p in (tmp_path / "snapshots").iterdir()] == ["nba_schedule.json"]


def test_sources_are_independent(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)

    a_at = store.put("a", {"v": "a"})
    store.put("b", {"v": "b"})

    assert store.get("a") == ({"v": "a"}, a_at)


def test_unreadable_snapshot_is_none(tmp_path: Path) -> None:
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "espn_scoreboard.json").write_text("{not json")

    assert SnapshotStore(tmp_path).get("espn_scoreboard") is None


def test_snapshot_without_fetched_at_is_none(tmp_path: Path) -> None:
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "espn_scoreboard.json").write_text('{"payload": {}}')

    assert SnapshotStore(tmp_path).get("espn_scoreboard") is None
