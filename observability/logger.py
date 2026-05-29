"""Append-only JSONL logger for turn records.

One JSON object per line -- trivially greppable and replayable. The log holds
generated code and a raw data snapshot, so it is a PII surface: the file is
created with 0600 permissions and is git-ignored. Production would add
redaction, a retention policy, and shipping to an audit store (see ADR-001).
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import asdict

import config
from observability.records import TurnRecord

# A turn record can exceed the pipe-atomic write size, and Streamlit runs each
# session in its own thread of one process -- so concurrent turns could
# interleave lines. A process-wide lock serialises the append. Module level
# (not per instance): a fresh TurnLogger is created per turn, all to one file.
_WRITE_LOCK = threading.Lock()


class TurnLogger:
    """Writes turn records as JSON lines to a local, owner-only file."""

    def __init__(self, log_path=None) -> None:
        self._path = log_path or config.EXECUTION_LOG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: TurnRecord) -> None:
        line = json.dumps(asdict(record), ensure_ascii=False)
        with _WRITE_LOCK:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        try:
            # 0600 -- the log is a PII surface.
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def read_all(self) -> list[dict]:
        """Read every logged turn record. A single corrupt or partially
        written line is skipped rather than failing the whole read."""
        if not self._path.exists():
            return []
        records: list[dict] = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
