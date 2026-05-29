---
status: complete
priority: p3
issue_id: 006
tags: [code-review, security, data, future-proofing]
dependencies: []
---

> **Resolved 2026-05-28 — Option B.** `EXAMPLE_CLIENT_NAME = "<example client>"`
> sentinel in `data/case.py`; `DataProfile.__post_init__` raises `ValueError`
> unless `example_row.client_name` matches it. Used the client_name marker (not
> the id) to avoid a real-case id collision. New test
> `test_profile_rejects_a_non_synthetic_example_row` proves the guard fires.

# Guard _EXAMPLE_ROW against future real-row substitution

## Problem Statement

`data/repository.py:36-41` defines `_EXAMPLE_ROW` as a hardcoded synthetic
`Case` with `id=9999` and `client_name="<example client>"`. It is
embedded **verbatim** into the system prompt via
`json.dumps(asdict(self.example_row))` (`data/profile.py:74`).

Today this is safe -- the constant is frozen. The latent risk is
that a "natural improvement" (showing a real recent case as the
example) would silently turn every string field of `example_row`
into a prompt-injection sink: a `client_name` of *"Ignore previous
instructions and ..."* would land directly in the system prompt.

This is a future-proofing guard, not an active vulnerability.

## Findings

Raised by /ce:review security-sentinel. Evidence:

- `data/repository.py:36-41` -- the synthetic constant
- `data/profile.py:74` -- the verbatim embedding into the system
  prompt (rendered string)
- `data/profile.py:48-50` -- `DataProfile.example_row: Case` (the
  type seam allows ANY `Case`, not just a synthetic one)

The risk is purely "what happens when someone wires `example_row`
to a real row in 6 months". A reviewer modifying this code would
NOT see a warning unless we put one there.

## Proposed Solutions

### Option A — Comment-only guard

Add a prominent comment on `_EXAMPLE_ROW` and on
`DataProfile.example_row` explaining: this field is verbatim-rendered
into the LLM system prompt; real client data MUST NEVER flow here.

- **Pros:** Zero runtime cost. Cheapest possible.
- **Cons:** Comments rot; depends on the next developer reading them.
- **Effort:** Trivial.
- **Risk:** None.

### Option B — Runtime assertion in DataProfile.__post_init__

`DataProfile` becomes a regular dataclass (not frozen) with
`__post_init__` that asserts `self.example_row.id == 9999`. Any
attempt to wire a real `Case` whose id isn't the magic-number
synthetic id raises at construction.

- **Pros:** Guard is enforced at runtime; can't be ignored.
- **Cons:** Tightly couples to a sentinel value; "id 9999" is now
  load-bearing magic. If a real case happens to have id 9999,
  legitimate test setups break.
- **Effort:** Small.
- **Risk:** Low. Could use a more specific sentinel
  (`client_name == "<example client>"` is harder to collide with).

### Option C — Type-level guard: separate `ExampleRow` type

Define a separate `ExampleRow` dataclass (different type from `Case`)
that the prompt builder reads. The conversion from a `Case` is
explicit and requires synthetic content.

- **Pros:** Strongest guard. Type checker prevents `Case` ever
  flowing into the prompt's example slot.
- **Cons:** Adds a class for a single use. Heavy for the actual risk
  level.
- **Effort:** Medium.
- **Risk:** Low but feels over-engineered for the threat.

## Recommended Action

_(Leave blank until triage.)_

## Technical Details

- **Threat model:** the threat is the *future* developer who wires
  this to real data, not a current attacker
- **Existing mitigation:** the synthetic `_EXAMPLE_ROW` constant
  with documented intent
- **Affected files:** `data/repository.py`, `data/profile.py`
- **No tests fail today regardless of choice**

## Acceptance Criteria

- [ ] One of A / B / C chosen and applied
- [ ] All tests pass (85+)
- [ ] If B: a test asserts that constructing a `DataProfile` with a
  non-synthetic `example_row` raises

## Work Log

- 2026-05-28: Raised by /ce:review security-sentinel finding #4.

## Resources

- ADR-001 §3 "The security model is the air-gap" -- the existing
  prompt-injection-via-case-data discussion
- `data/repository.py:36-41` (`_EXAMPLE_ROW`)
- `data/profile.py:48-74` (`DataProfile.example_row`, renderer)
