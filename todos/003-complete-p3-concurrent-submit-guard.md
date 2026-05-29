---
status: complete
priority: p3
issue_id: "003"
tags: [code-review, concurrency, streamlit]
dependencies: []
---

# Concurrent submit-while-running has no guard on shared `_contents`

## Problem Statement

`GeminiClient._contents` is mutated by `send_user` / `send_tool_results`.
With the session-cached client, the list is shared across turns within one
Streamlit session. The list is **not** lock-guarded. Streamlit's per-session
single-threaded execution normally serialises this, but the new `with
st.status(...)` block + `st.write_stream` involve long synchronous waits
during which a user clicking submit again could (theoretically) cause two
`run_turn` generators to advance against the same client.

Today: unlikely to fire — Streamlit's submit handler blocks while the
script is running. But the data structure is now shared and unguarded,
and the ADR's "conversation memory across turns" promise quietly assumes
serial execution. Making the assumption explicit is cheap.

## Findings

- `agent/llm_gemini.py:67-78` — mutation of `self._contents` is unprotected.
- `app.py:103-108` — no busy-flag on `st.chat_input` while a turn runs.

## Proposed Solutions

### Option A: Disable `st.chat_input` while a turn is in flight

Set `st.session_state.busy = True` before `run_turn`, clear it after.
Disable the chat input when busy.

- **Pros:** UX matches reality — user can see they can't submit. No code in `GeminiClient`. Simplest.
- **Cons:** None significant.
- **Effort:** Small (~5 lines).
- **Risk:** Very low.

### Option B: `threading.Lock` in `GeminiClient`

- **Pros:** Defense at the source.
- **Cons:** Hides the UX issue (user can submit but gets queued silently with a long wait); cost of lock acquisition on a path that's already serial in practice.
- **Effort:** Small.
- **Risk:** Low.

## Recommended Action

**Option A** — fixes the actual user experience and is the simplest. The
data structure stays unguarded but the UI prevents the concurrent-submit
path from being reachable.

## Acceptance Criteria

- [x] `st.chat_input` is disabled (or otherwise blocked) while a turn is in flight.
- [ ] Manually verify in the Streamlit UI that submit is unresponsive during a run.

## Work Log

- **2026-05-24** — Implemented Option A. Added `st.session_state.busy`
  initialised on first render; `st.chat_input` is constructed with
  `disabled=st.session_state.busy`. The whole prompt-handling block is
  wrapped in `try/finally` so `busy` clears on `st.stop()` and any
  uncaught error. A comment at the input call states the Streamlit-
  serialisation assumption explicitly.

## Resources

- Reviewer: `architecture-strategist` P3.
