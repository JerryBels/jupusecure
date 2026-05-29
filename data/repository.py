"""Case data access.

The repository is the only component that reads raw case rows. It serves two
distinct, deliberately separated outputs:

  * ``cases_json()``  -- the raw rows, injected ONLY into the air-gapped
    sandbox so snippets can compute over them. The wire format is JSON dicts
    because that's what the LLM was prompted to consume; the typed ``Case``
    objects are projected to dicts at the boundary.
  * ``data_profile()`` -- schema + aggregate shape (enum values, date ranges,
    null counts, a synthetic example row). This is the ONLY case-derived
    information that reaches the LLM. Raw client data never enters a prompt,
    which removes the prompt-injection vector where a crafted field value
    could steer generated code.

POC scoping note: ``all_cases()`` returns the full caseload. In production this
is the seam where retrieval (pgvector / RAG) returns only the rows a query
needs; the rest of the system is unaffected.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from data.case import EXAMPLE_CLIENT_NAME, Case
from data.profile import DataProfile, DateRange
from data.seed import CASE_FIELDS, REFERENCE_TODAY

# Fields whose distinct values are small, closed sets worth listing for the LLM.
_CATEGORICAL_FIELDS = ("category", "status", "outcome")

# Fixed synthetic example shown to the LLM. Never a real client -- the
# EXAMPLE_CLIENT_NAME marker is what DataProfile asserts on.
_EXAMPLE_ROW = Case(
    id=9999, client_name=EXAMPLE_CLIENT_NAME, category="Litigation",
    status="open", filing_date="2026-02-01", hearing_date="2026-07-15",
    closing_date=None, claim_amount="100000.00", fine_amount=None,
    outcome="pending",
)


class CaseRepository:
    """Read access to the mock legal-case dataset.

    The Repository is pure read: it does not bootstrap or write. Database
    creation is the entrypoint's job (see ``app.py`` / ``scripts/cli.py``),
    not a side effect of constructing a Repository. The path is always
    passed in -- no implicit default, no reach into ``config``.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # -- raw data (sandbox-only) ---------------------------------------------
    def all_cases(self) -> list[Case]:
        """Return every case as a ``Case``. Production seam: scope this via RAG.

        `closing()` is required: a sqlite3 connection used as a plain context
        manager commits but does NOT close -- in a long-running process that
        leaks a connection per call.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM cases ORDER BY id").fetchall()
        return [Case(**dict(row)) for row in rows]

    def cases_json(self) -> str:
        """Serialize the raw rows for injection into the sandbox.

        Snippets see a list of plain dicts because that's the contract the
        LLM was prompted to write against -- ``case['claim_amount']`` style.
        """
        return json.dumps([asdict(case) for case in self.all_cases()])

    # -- profile (LLM-facing) -------------------------------------------------
    def data_profile(self) -> DataProfile:
        """Schema + aggregate shape. The only case-derived data the LLM sees.

        Returns a ``DataProfile`` (typed) rather than a dict so the prompt
        builder doesn't reach into our internal structure -- the rendering
        belongs to the Caseload context that owns the data shape.
        """
        cases = self.all_cases()
        return DataProfile(
            reference_today=REFERENCE_TODAY,
            row_count=len(cases),
            schema=list(CASE_FIELDS),
            categorical_values={
                field.name: sorted({getattr(case, field.name) for case in cases})
                for field in CASE_FIELDS if field.name in _CATEGORICAL_FIELDS
            },
            date_ranges=self._date_ranges(cases),
            null_counts={
                field.name: sum(1 for case in cases
                                if getattr(case, field.name) is None)
                for field in CASE_FIELDS if field.nullable
            },
            example_row=_EXAMPLE_ROW,
        )

    @staticmethod
    def _date_ranges(cases: list[Case]) -> dict[str, DateRange | None]:
        # Date columns are derived from the schema (type == "date"), the same
        # single source of truth null_counts uses -- no parallel field list to
        # drift out of sync when a date column is added.
        ranges: dict[str, DateRange | None] = {}
        for field in CASE_FIELDS:
            if field.type != "date":
                continue
            values = sorted(getattr(case, field.name) for case in cases
                            if getattr(case, field.name) is not None)
            ranges[field.name] = (DateRange(min=values[0], max=values[-1])
                                  if values else None)
        return ranges
