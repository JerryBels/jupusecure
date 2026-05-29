"""Tests for the turn logger.

Covers the concurrency safety of the append (Streamlit runs each session in
its own thread), tolerance of a corrupt line on read, and the owner-only file
mode (the log is a PII surface).
"""

from __future__ import annotations

import stat
import threading

from observability.logger import TurnLogger
from observability.records import TurnRecord, new_turn_id


def _record(i: int) -> TurnRecord:
    # A large snapshot makes the line exceed the pipe-atomic write size, so
    # concurrent unsynchronised appends would genuinely interleave.
    return TurnRecord(
        turn_id=new_turn_id(), session_id=f"s{i}",
        timestamp="2026-05-22T00:00:00Z", user_query=f"query {i}",
        data_snapshot="x" * 8000,
    )


def test_concurrent_logging_produces_intact_lines(tmp_path):
    logger = TurnLogger(tmp_path / "log.jsonl")
    count = 24
    threads = [threading.Thread(target=lambda i=i: logger.log(_record(i)))
               for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = logger.read_all()
    assert len(records) == count
    assert ({record["session_id"] for record in records}
            == {f"s{i}" for i in range(count)})


def test_read_all_skips_a_corrupt_line(tmp_path):
    path = tmp_path / "log.jsonl"
    logger = TurnLogger(path)
    logger.log(_record(1))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")
    logger.log(_record(2))

    records = logger.read_all()
    assert len(records) == 2
    assert {record["session_id"] for record in records} == {"s1", "s2"}


def test_log_file_is_owner_only(tmp_path):
    path = tmp_path / "log.jsonl"
    TurnLogger(path).log(_record(1))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_read_all_is_empty_when_no_log_exists(tmp_path):
    assert TurnLogger(tmp_path / "missing.jsonl").read_all() == []
