---
status: complete
priority: p2
issue_id: 005
tags: [code-review, architecture, simplicity, data]
dependencies: []
---

> **Resolved 2026-05-28 — Option A.** Dropped `SchemaField`; `DataProfile.schema`
> is now `list[CaseField]` and the repository passes `list(CASE_FIELDS)` directly.
> The renderer keeps ignoring `.sql`. −12 lines, drift risk gone.

# Collapse SchemaField, or add CaseField.to_schema_field() projection

## Problem Statement

`data/profile.py:18-25` defines `SchemaField` with four fields
(`name`, `type`, `nullable`, `description`). `data/case.py:20-27`
defines `CaseField` with the same four fields plus `sql`.
`CaseRepository.data_profile()` (`data/repository.py:78-82`) projects
`CaseField` → `SchemaField` field-by-field in a list comp.

Two reviewers flagged this independently:

- **architecture-strategist**: the manual field-by-field projection
  silently truncates if `SchemaField` ever gains a field that
  `CaseField` doesn't. Drift risk.
- **code-simplicity-reviewer**: `SchemaField` adds ~12 lines for a
  speculative "descriptions can differ from SQL-source descriptions
  if we ever want to" YAGNI. There's one description today and one
  description in every reasonable future.

## Findings

- `SchemaField` exists only because we wanted to avoid leaking `sql`
  to the LLM-facing profile. But the LLM renderer (`render_for_llm`,
  `data/profile.py:60-65`) doesn't read `.sql` anyway -- it could take
  `CaseField` directly and ignore that field.
- `description` is identical on both sides today. The "may differ"
  comment is speculative.
- No test asserts the projection round-trip; if a new `CaseField`
  field were added, only `seed.py` and `case.py` would need updating.
  Projecting today requires also touching `profile.py` and
  `repository.py`.

Evidence:

- `data/case.py:20-27` — `CaseField` definition
- `data/profile.py:18-25` — `SchemaField` definition
- `data/profile.py:21-23` — the "if descriptions could differ" comment
- `data/repository.py:78-82` — the manual projection list-comp

## Proposed Solutions

### Option A — Drop `SchemaField`; `DataProfile.schema: list[CaseField]`

`DataProfile.schema` becomes `list[CaseField]`. The LLM renderer
keeps ignoring `.sql`. The projection list-comp in `repository.py`
collapses to `schema=list(CASE_FIELDS)`.

- **Pros:** ~12 lines saved. Drift risk eliminated. One concept
  ("a case field") instead of two ("a case field" and "the
  LLM-facing projection of a case field").
- **Cons:** The Caseload-internal vs LLM-facing distinction stops
  being type-enforced -- any future need to hide a field from the
  LLM would require either a new projection or filtering in the
  renderer.
- **Effort:** Small (~10 minutes; one comprehension, one
  type-annotation, possibly a test fixture update).
- **Risk:** Low. If we ever genuinely want different LLM-facing
  descriptions, reintroducing a projection is a 5-minute change.

### Option B — Keep `SchemaField`; add `CaseField.to_schema_field()`

`CaseField` gains a method `to_schema_field() -> SchemaField` that
encapsulates the projection. The repository call becomes
`schema=[f.to_schema_field() for f in CASE_FIELDS]`. If
`CaseField` adds a field, the projection lives next to the source.

- **Pros:** Preserves the bounded-context separation (Caseload-internal
  vs Conversation-facing). Drift is mitigated -- adding a new
  `CaseField` field forces a decision about whether it appears in
  `SchemaField`.
- **Cons:** Still ~12 lines of `SchemaField` class kept for a
  speculative divergence that doesn't exist today.
- **Effort:** Small.
- **Risk:** None.

### Option C — Status quo

Document that the two are the projection of each other; add a test
that asserts the two classes have the expected fields and the
projection is correct.

- **Pros:** Zero behavior change.
- **Cons:** Extra test code; doesn't fix the underlying smell.

## Recommended Action

_(Leave blank until triage.)_

## Technical Details

- **Affected files (Option A):** `data/profile.py`, `data/repository.py`,
  possibly `tests/test_prompts.py` (fixture `_profile`)
- **Affected files (Option B):** `data/case.py`, `data/repository.py`
- **Affected files (Option C):** `tests/test_data.py` (new test)
- **No DB / wire / API changes** under any option.

## Acceptance Criteria

- [ ] One of A / B / C chosen and applied
- [ ] All tests pass (85+)
- [ ] If A: `SchemaField` is gone; `DataProfile.schema: list[CaseField]`
- [ ] If B: `CaseField.to_schema_field()` is the single projection site
- [ ] If C: a test asserts the projection contract

## Work Log

- 2026-05-28: Raised by /ce:review code-simplicity-reviewer +
  architecture-strategist (independent findings).

## Resources

- ADR-001 §"Decision" -- the Caseload bounded-context design
- `data/case.py`, `data/profile.py`, `data/repository.py`
