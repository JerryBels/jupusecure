---
status: complete
priority: p3
issue_id: "004"
tags: [code-review, architecture, layering]
dependencies: []
---

# `build_session_client` placement is borderline (orchestrator vs session module)

## Problem Statement

`build_session_client` lives in `agent/orchestrator.py`. Its job is to
assemble a session-lifetime client (system prompt + tools + provider
factory). The orchestrator module's docstring claims it is "the per-turn
loop" and "provider-agnostic" — yet it now also hosts a session-lifetime
factory that imports from `agent.prompts` and `agent.tools`.

Functional today, but a smell that compounds when a second tool is added or
when prompt construction becomes more dynamic.

## Findings

- `agent/orchestrator.py:112-128` — `build_session_client` in the per-turn
  loop module.
- `app.py:20-21` — imports `build_session_client` from a module called
  `orchestrator`, which reads slightly odd to a fresh reader.

## Proposed Solutions

### Option A: Leave it

Acknowledge as borderline; no functional problem. The simplicity reviewer
explicitly concluded this is "not overbuilt."

### Option B: Move to a new `agent/session.py`

Clean separation: orchestrator owns turns, session module owns
session-lifetime construction.

- **Pros:** Layering is explicit. Future "second tool" or "dynamic prompt
  rebuild" naturally lands there.
- **Cons:** More files for what is currently one function.
- **Effort:** Small.
- **Risk:** None.

### Option C: Fold into `agent/llm.py` as a default-args helper

Cleanest from a "construction helpers near the abstraction" angle.

- **Pros:** Co-located with `LLMClient`.
- **Cons:** `agent/llm.py` was designed to be tiny; pulling in
  `build_system_prompt` and `EXECUTE_PYTHON_TOOL` would expand its
  responsibility.

## Recommended Action

**Option A (leave)** for the takehome submission — the smell is real but
the cost of restructuring exceeds the value for a beta. Revisit if a second
tool or a second provider lands.

## Acceptance Criteria

- [x] Decision recorded (no code change required for Option A).

## Work Log

- **2026-05-24** — Decision: **Option A (leave)**. The smell is real but
  the cost of restructuring exceeds the value for a beta-quality
  takehome submission. The simplicity reviewer independently concluded
  "not overbuilt." Revisit if a second tool or a second provider lands —
  that's when the layering tension actually compounds.

## Resources

- Reviewer: `architecture-strategist` P3 second item.
- Simplicity reviewer: "borderline, not overbuilt; not flagged."
