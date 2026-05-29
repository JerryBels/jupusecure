"""Tests for the system-prompt builder and the post-hoc routing guard."""

from __future__ import annotations

from agent.prompts import build_system_prompt, looks_like_unrouted_computation
from data.case import EXAMPLE_CLIENT_NAME, Case, CaseField
from data.profile import DataProfile


def _example_case() -> Case:
    return Case(id=1, client_name=EXAMPLE_CLIENT_NAME, category="IP",
                status="open", filing_date="2026-01-01", hearing_date=None,
                closing_date=None, claim_amount="0.00", fine_amount=None,
                outcome="pending")


def _profile(**overrides) -> DataProfile:
    defaults = dict(
        reference_today="2026-05-21",
        row_count=0,
        schema=[CaseField(name="id", sql="INTEGER", type="int",
                          nullable=False, description="case number")],
        categorical_values={"status": [], "category": [], "outcome": []},
        date_ranges={"filing_date": None, "hearing_date": None,
                     "closing_date": None},
        null_counts={},
        example_row=_example_case(),
    )
    defaults.update(overrides)
    return DataProfile(**defaults)


def test_build_system_prompt_handles_an_empty_caseload():
    """An empty dataset (no enum values, no date ranges) must not crash."""
    prompt = build_system_prompt(_profile())
    assert isinstance(prompt, str) and len(prompt) > 100
    assert "2026-05-21" in prompt


def test_build_system_prompt_lists_enum_values():
    prompt = build_system_prompt(_profile(categorical_values={
        "status": ["open", "closed"], "category": ["IP"], "outcome": ["won"]}))
    assert "open" in prompt and "closed" in prompt


def test_build_system_prompt_states_the_routing_policy():
    prompt = build_system_prompt(_profile())
    assert "execute_python" in prompt
    assert "ROUTING POLICY" in prompt


def test_routing_guard_flags_a_count():
    assert looks_like_unrouted_computation("There are 5 open cases.")


def test_routing_guard_flags_currency():
    assert looks_like_unrouted_computation("The total comes to €1,000.")


def test_routing_guard_ignores_pure_prose():
    assert not looks_like_unrouted_computation(
        "A provision is a financial reserve held against a potential liability.")
