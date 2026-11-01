from __future__ import annotations

import json

import query_history as qh

NOW = 1_800_000_000.0
DAY = 86400.0


def entries(path) -> list[dict]:
    return json.loads((path / qh.HISTORY_FILE).read_text(encoding="utf-8"))


def test_record_appends_an_entry(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW)
    assert entries(tmp_path) == [{"stock_id": "2330", "ts": NOW}]


def test_record_trims_entries_past_the_window(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW - 8 * DAY)
    qh.record(tmp_path, "2317", now=NOW)
    assert [e["stock_id"] for e in entries(tmp_path)] == ["2317"]


def test_one_page_query_counts_once(tmp_path) -> None:
    """/latest runs twice per page query: directly and through /quick_summary."""
    qh.record(tmp_path, "2330", now=NOW)
    qh.record(tmp_path, "2330", now=NOW + 1)
    assert len(entries(tmp_path)) == 1


def test_later_queries_of_the_same_stock_count_again(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW)
    qh.record(tmp_path, "2330", now=NOW + 120)
    assert len(entries(tmp_path)) == 2


def test_record_leaves_no_temp_file(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW)
    assert [p.name for p in tmp_path.iterdir()] == [qh.HISTORY_FILE]


def test_top_recent_orders_by_count(tmp_path) -> None:
    for offset in (0, 100, 200):
        qh.record(tmp_path, "2317", now=NOW + offset)
    qh.record(tmp_path, "2330", now=NOW + 300)
    assert qh.top_recent(tmp_path, now=NOW + 400) == ["2317", "2330"]


def test_top_recent_breaks_ties_by_most_recent(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW)
    qh.record(tmp_path, "2317", now=NOW + 100)
    assert qh.top_recent(tmp_path, now=NOW + 200) == ["2317", "2330"]


def test_top_recent_applies_the_limit(tmp_path) -> None:
    for index, sid in enumerate(("1101", "1216", "2330")):
        qh.record(tmp_path, sid, now=NOW + index)
    assert len(qh.top_recent(tmp_path, limit=2, now=NOW + 10)) == 2


def test_top_recent_ignores_old_entries(tmp_path) -> None:
    qh.record(tmp_path, "2330", now=NOW)
    assert qh.top_recent(tmp_path, now=NOW + 8 * DAY) == []


def test_corrupt_file_counts_as_empty(tmp_path) -> None:
    (tmp_path / qh.HISTORY_FILE).write_text("{not json", encoding="utf-8")
    assert qh.top_recent(tmp_path, now=NOW) == []
    qh.record(tmp_path, "2330", now=NOW)
    assert [e["stock_id"] for e in entries(tmp_path)] == ["2330"]


def test_unexpected_shape_counts_as_empty(tmp_path) -> None:
    (tmp_path / qh.HISTORY_FILE).write_text('{"stock_id": "2330"}', encoding="utf-8")
    assert qh.top_recent(tmp_path, now=NOW) == []


def test_missing_file_counts_as_empty(tmp_path) -> None:
    assert qh.top_recent(tmp_path, now=NOW) == []
