"""Print logged turn records (most recent first).

Defaults to the last record; pass ``-n N`` for the last N. The bulky
``data_snapshot`` field is elided -- ``data_snapshot_hash`` is kept for
identifying which snapshot was used, and the raw JSONL file
(``logs/executions.jsonl``) has the full content if you need it for replay.

Run:  python scripts/show_log.py           # last record
      python scripts/show_log.py -n 5      # last 5 records
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from observability.logger import TurnLogger  # noqa: E402


def main(argv: list[str]) -> int:
    count = 1
    if len(argv) >= 3 and argv[1] in ("-n", "--count"):
        try:
            count = max(1, int(argv[2]))
        except ValueError:
            print("usage: python scripts/show_log.py [-n N]")
            return 2

    records = TurnLogger().read_all()
    if not records:
        print("(no log records yet — run a turn first)")
        return 0

    for record in records[-count:]:
        record = {k: v for k, v in record.items() if k != "data_snapshot"}
        print(json.dumps(record, indent=2, ensure_ascii=False))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
