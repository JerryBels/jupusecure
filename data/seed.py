"""Mock legal-case dataset.

This module is the single source of truth for the case schema (CASE_FIELDS).
The repository derives the LLM-facing schema description from it, so the schema
is never hand-written in more than one place.

Money is stored and emitted as strings, never floats: the whole point of the
code-execution feature is exact decimal arithmetic, so the data forces snippets
to wrap amounts in ``Decimal``. Dates are ISO strings. ``hearing_date`` and
``closing_date`` are intentionally null for some rows so snippets must handle
missing dates.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from data.case import CaseField

# --- Single schema source ----------------------------------------------------
CASE_FIELDS: list[CaseField] = [
    CaseField("id", "INTEGER PRIMARY KEY", "int", False,
              "unique case number"),
    CaseField("client_name", "TEXT NOT NULL", "str", False,
              "the client we represent"),
    CaseField("category", "TEXT NOT NULL", "str", False,
              "practice area"),
    CaseField("status", "TEXT NOT NULL", "str", False,
              "'open' or 'closed'"),
    CaseField("filing_date", "TEXT NOT NULL", "date", False,
              "ISO date the case was filed"),
    CaseField("hearing_date", "TEXT", "date", True,
              "ISO date of the hearing; null when not yet scheduled"),
    CaseField("closing_date", "TEXT", "date", True,
              "ISO date the case closed; null for open cases"),
    CaseField("claim_amount", "TEXT NOT NULL", "decimal", False,
              "claim value in EUR, as a decimal string"),
    CaseField("fine_amount", "TEXT", "decimal", True,
              "fine in EUR as a decimal string; null when no fine"),
    CaseField("outcome", "TEXT NOT NULL", "str", False,
              "'won', 'lost', 'settled', or 'pending'"),
]

# --- Seed rows ---------------------------------------------------------------
# Hand-checked so every example query returns a verifiable answer. The fixed
# reference "today" for the system prompt is 2026-05-21.
#   - Q1-2026 filings (6 cases) sum to a claim total of 1,085,000 EUR.
#   - Q1-2025 filings (6 cases) sum to a claim total of   833,000 EUR.
#   - Case #4821 has a 45,000 EUR fine -> provision (30%) = 13,500 EUR.
#   - Cases #4827 and #4848 are open with a null hearing_date.
_CASES: list[tuple] = [
    # id   client                 category       status    filing        hearing       closing       claim        fine       outcome
    (4801, "Helmut Bauer",         "Real Estate", "closed", "2025-01-15", "2025-04-10", "2025-05-20", "120000.00", None,      "won"),
    (4802, "Aurelia Stein",        "Litigation",  "closed", "2025-02-03", "2025-06-01", "2025-07-15", "85000.00",  "12000.00","lost"),
    (4805, "Klaus Vogel",          "Real Estate", "closed", "2025-03-20", "2025-07-02", "2025-08-10", "240000.00", None,      "settled"),
    (4810, "Marlene Roth",         "Corporate",   "closed", "2025-02-18", "2025-05-12", "2025-06-30", "310000.00", None,      "won"),
    (4812, "Tomas Werner",         "IP",          "closed", "2025-03-05", "2025-09-01", "2025-10-20", "56000.00",  "8000.00", "lost"),
    (4815, "Ingrid Hoffmann",      "Family",      "closed", "2025-01-28", "2025-04-15", "2025-05-30", "22000.00",  None,      "settled"),
    (4818, "Dieter Lang",          "Litigation",  "closed", "2024-11-10", "2025-02-20", "2025-03-25", "95000.00",  "15000.00","lost"),
    (4820, "Sabine Keller",        "Real Estate", "closed", "2024-12-05", "2025-03-18", "2025-12-15", "180000.00", None,      "won"),
    (4821, "Bauer & Soehne GmbH",  "Corporate",   "closed", "2026-01-12", "2026-03-30", "2026-04-28", "150000.00", "45000.00","lost"),
    (4823, "Petra Schulz",         "Litigation",  "open",   "2026-01-20", "2026-06-15", None,         "78000.00",  None,      "pending"),
    (4825, "Andreas Moeller",      "Real Estate", "open",   "2026-02-08", "2026-07-01", None,         "265000.00", None,      "pending"),
    (4827, "Lena Fischer",         "IP",          "open",   "2026-02-25", None,         None,         "42000.00",  None,      "pending"),
    (4830, "Rolf Zimmermann",      "Corporate",   "closed", "2026-03-14", "2026-05-02", "2026-05-19", "520000.00", None,      "won"),
    (4832, "Heike Brandt",         "Family",      "open",   "2026-03-22", "2026-06-30", None,         "30000.00",  None,      "pending"),
    (4835, "Markus Wolf",          "Litigation",  "closed", "2025-12-01", "2026-03-10", "2026-04-15", "110000.00", "20000.00","settled"),
    (4838, "Claudia Neumann",      "Real Estate", "closed", "2025-11-18", "2026-02-28", "2026-04-05", "198000.00", None,      "won"),
    (4840, "Stefan Krause",        "IP",          "open",   "2026-04-02", "2026-08-12", None,         "67000.00",  None,      "pending"),
    (4842, "Nina Hartmann",        "Corporate",   "closed", "2025-10-22", "2026-01-30", "2026-03-12", "410000.00", None,      "settled"),
    (4845, "Georg Schmitt",        "Family",      "closed", "2026-04-18", None,         "2026-05-10", "18000.00",  None,      "won"),
    (4848, "Ursula Wagner",        "Litigation",  "open",   "2026-05-02", None,         None,         "91000.00",  None,      "pending"),
]

REFERENCE_TODAY = "2026-05-21"


def ensure_seeded(db_path: Path) -> None:
    """Seed ``db_path`` if it doesn't exist yet; prints one line if it did.

    Called by the entrypoints (``app.py``, ``scripts/cli.py``) so the
    Repository constructor stays a pure read with no setup side effect.
    """
    if db_path.exists():
        return
    seed(db_path)
    print(f"[setup] Seeded mock caseload at {db_path}")


def seed(db_path: Path) -> None:
    """(Re)create the SQLite database from CASE_FIELDS and the seed rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ", ".join(f"{field.name} {field.sql}" for field in CASE_FIELDS)
    field_names = ", ".join(field.name for field in CASE_FIELDS)
    placeholders = ", ".join("?" for _ in CASE_FIELDS)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DROP TABLE IF EXISTS cases")
        conn.execute(f"CREATE TABLE cases ({columns})")
        conn.executemany(
            f"INSERT INTO cases ({field_names}) VALUES ({placeholders})",
            _CASES,
        )
        conn.commit()


if __name__ == "__main__":
    import config

    seed(config.CASES_DB_PATH)
    print(f"Seeded {len(_CASES)} cases into {config.CASES_DB_PATH}")
