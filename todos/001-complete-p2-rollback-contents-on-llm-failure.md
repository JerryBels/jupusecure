---
status: complete
priority: p2
issue_id: "001"
tags: [code-review, architecture, agent, correctness]
dependencies: []
---

# Roll back `GeminiClient._contents` when `_step()` raises

## Problem Statement

`GeminiClient.send_user(text)` appends a user `Content` to `self._contents`
**before** calling the API. If the API raises (translated to `LLMAuthError`,
`LLMQuotaError`, `LLMUnavailableError`, or generic `LLMError`), the orphan
user message stays in `_contents` and no model response follows it. The same
asymmetry exists in `send_tool_results`.

Was harmless pre-cache (the whole `GeminiClient` was thrown away at end of
turn). **The new session-caching from commit `91a6118`** is what makes this
a real defect: the next `send_user` appends another user message right after
the orphan one, so the provider sees `[user, user, …]` with no intervening
model turn.

Adjacent consequence (the agent-native reviewer's P3 #3): when an LLM error
fires, the model never gets an assistant turn for the failed question. A
user saying "try again" next has the model staring at their prior question
with no answer. Rolling back the orphan user fixes both — a clean rollback
means the failed turn is *as if it never happened* from the model's view.

## Findings

- **Source — `agent/llm_gemini.py:67-78`** — `send_user` and
  `send_tool_results` append before the call:
  ```python
  def send_user(self, text):
      self._contents.append(types.Content(role="user", parts=[...]))
      return self._step()  # may raise; user message is now orphaned
  ```
- **Caching change — `app.py:103-107`** — the same `GeminiClient` instance
  serves every turn in the session, so any orphan stays for the rest of the
  session.
- **No regression test** — the orchestrator's `test_llm_api_error_is_handled_gracefully`
  uses a fake client that doesn't model `_contents`; this defect isn't
  exercised.

## Proposed Solutions

### Option A: Try/except rollback inside `send_user` / `send_tool_results`

```python
def send_user(self, text):
    content = types.Content(role="user", parts=[types.Part.from_text(text=text)])
    self._contents.append(content)
    try:
        return self._step()
    except Exception:
        self._contents.pop()  # roll back: the call never happened
        raise
```

- **Pros:** Minimal change. Behaviour is exactly "failed turn never happened
  from the model's perspective." Symmetric fix in both methods.
- **Cons:** Loses the "try again" affordance — the model won't see the
  failed question on retry either. Acceptable since the *user* sees the
  full failed-turn context in the Streamlit history.
- **Effort:** Small (~10 lines + test).
- **Risk:** Low.

### Option B: Append model "failure note" instead of rolling back

Translate the exception into a synthetic `types.Content(role="model", parts=[Part.from_text("[provider error: ...]")])` and append it. The conversation stays intact; on retry the model sees both the question and a note that it failed.

- **Pros:** Preserves "try again" semantics — the model knows what failed.
- **Cons:** More code, requires a sanitised message string (the raw provider error may leak details). Now also need to decide what the model sees ("api key invalid" tells it nothing useful; "I couldn't reach the provider, retry the question" might encourage retry loops).
- **Effort:** Medium.
- **Risk:** Medium — synthetic model turns can confuse subsequent reasoning.

### Option C: Invalidate `st.session_state.llm_client` on LLM error

When the orchestrator catches an `LLMError`, signal `app.py` to drop the
cached client. Next turn starts fresh.

- **Pros:** Side-steps the asymmetry entirely.
- **Cons:** Loses ALL conversation memory on any transient provider error
  (a 429 wipes a 20-message session). Bad UX.
- **Effort:** Small.
- **Risk:** Low (correctness) / High (UX).

## Recommended Action

**Option A** — clean, minimal, addresses both this finding and the related
"model continuity on retry" P3 from the agent-native review.

## Technical Details

- **Files:** `agent/llm_gemini.py` (`send_user`, `send_tool_results`).
- **Tests:** add a regression test that builds a `GeminiClient` with a
  monkey-patched `_step` raising `APIError`, calls `send_user`, asserts
  `_contents` length unchanged.

## Acceptance Criteria

- [ ] `send_user` rolls back its appended `Content` if `_step()` raises any exception.
- [ ] `send_tool_results` rolls back identically.
- [ ] Regression test asserts `_contents` length is preserved through a failed call.
- [ ] All 79 existing tests still pass.

## Work Log

- **2026-05-24** — Implemented Option A (try/except rollback). Both
  `send_user` and `send_tool_results` now pop the just-appended `Content`
  if `_step()` raises. Added regression tests in
  `tests/test_llm_gemini.py`: `test_send_user_rolls_back_on_failure` and
  `test_send_tool_results_rolls_back_on_failure`. 83/83 tests pass.

## Resources

- Reviewers: `security-sentinel` P2-1, `agent-native-reviewer` finding #2.
- Related commit: `91a6118` (session-client caching).
