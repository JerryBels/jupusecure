"""The Caseload domain's value objects.

A *Case* is the domain entity the lawyer reasons about. A *CaseField* describes
one column of the case schema -- it's the single source of truth used to build
the SQLite table, derive the LLM-facing schema description, and check
nullability when computing the data profile.

These replace the loose ``dict`` shapes the earlier POC carried. Snippets
running in the sandbox still see plain dicts because that's the wire format
(``cases_json``) the LLM was prompted to consume; on the host every layer
above the wire deals in typed objects.
"""

from __future__ import annotations

from dataclasses import dataclass

# Marks a Case as synthetic (the example row shown to the LLM). The angle
# brackets make it unmistakably a placeholder, never a real client name.
# DataProfile asserts the example row carries this, so real client data can
# never be rendered into the system prompt. See ADR-001 §3.
EXAMPLE_CLIENT_NAME = "<example client>"


@dataclass(frozen=True)
class CaseField:
    """One column of the case schema -- source of truth for SQL + LLM docs."""

    name: str
    sql: str            # e.g. "TEXT NOT NULL" -- the CREATE TABLE fragment
    type: str           # e.g. "decimal" -- the conceptual type the LLM sees
    nullable: bool
    description: str    # human-readable, shown to the LLM verbatim


@dataclass(frozen=True)
class Case:
    """A single legal case. Sandbox snippets receive a dict view (`asdict`)."""

    id: int
    client_name: str
    category: str
    status: str
    filing_date: str            # ISO date
    hearing_date: str | None    # ISO date or null when not yet scheduled
    closing_date: str | None    # ISO date or null when still open
    claim_amount: str           # decimal string -- never a float
    fine_amount: str | None     # decimal string or null when no fine
    outcome: str
