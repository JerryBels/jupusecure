"""Tests for the mock data layer and the LLM-facing data profile."""

from __future__ import annotations

from decimal import Decimal

import pytest

from data.case import Case
from data.profile import DataProfile
from data.repository import CaseRepository
from data.seed import seed


def _repo(tmp_path):
    db = tmp_path / "cases.db"
    seed(db)
    return CaseRepository(db)


def test_seed_loads_every_case(tmp_path):
    assert len(_repo(tmp_path).all_cases()) == 20


def test_profile_lists_categorical_values(tmp_path):
    profile = _repo(tmp_path).data_profile()
    assert set(profile.categorical_values["status"]) == {"open", "closed"}
    assert "Real Estate" in profile.categorical_values["category"]


def test_profile_never_contains_a_real_client_name(tmp_path):
    """The profile is the only case-derived data the LLM sees -- raw client
    names must never appear in it (prompt-injection / privacy). Also asserts
    that what the LLM ultimately reads (render_for_llm) is clean."""
    repo = _repo(tmp_path)
    profile = repo.data_profile()
    real_names = {case.client_name for case in repo.all_cases()}
    rendered = profile.render_for_llm()
    assert not any(name in rendered for name in real_names)


def test_profile_reports_nullable_field_counts(tmp_path):
    profile = _repo(tmp_path).data_profile()
    assert profile.null_counts["hearing_date"] == 3
    assert profile.null_counts["fine_amount"] == 15


def test_example_queries_have_verifiable_answers(tmp_path):
    """Anchors the four brief example queries to known expected values."""
    cases = _repo(tmp_path).all_cases()

    q1_2026 = sum(Decimal(case.claim_amount) for case in cases
                  if "2026-01-01" <= case.filing_date <= "2026-03-31")
    q1_2025 = sum(Decimal(case.claim_amount) for case in cases
                  if "2025-01-01" <= case.filing_date <= "2025-03-31")
    assert q1_2026 == Decimal("1085000.00")
    assert q1_2025 == Decimal("833000.00")

    case_4821 = next(case for case in cases if case.id == 4821)
    provision = Decimal(case_4821.fine_amount) * Decimal("0.30")
    assert provision == Decimal("13500.00")


def test_cases_json_round_trips(tmp_path):
    """The JSON wire format is the dict view of every Case -- snippets in
    the sandbox were prompted to consume ``case['claim_amount']`` style."""
    import json
    from dataclasses import asdict
    repo = _repo(tmp_path)
    assert (json.loads(repo.cases_json())
            == [asdict(case) for case in repo.all_cases()])


def test_profile_rejects_a_non_synthetic_example_row(tmp_path):
    """The example row is rendered verbatim into the system prompt, so a
    DataProfile carrying a real client name must fail to construct -- the
    air-gap guard (ADR-001 Section 3)."""
    real_case = Case(
        id=1, client_name="Acme GmbH", category="IP", status="open",
        filing_date="2026-01-01", hearing_date=None, closing_date=None,
        claim_amount="0.00", fine_amount=None, outcome="pending",
    )
    with pytest.raises(ValueError, match="must be synthetic"):
        DataProfile(
            reference_today="2026-05-21", row_count=0, schema=[],
            categorical_values={}, date_ranges={}, null_counts={},
            example_row=real_case,
        )
