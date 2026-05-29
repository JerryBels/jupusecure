---
status: complete
priority: p2
issue_id: "002"
tags: [code-review, observability, integrity, audit]
dependencies: []
---

# Audit log records a system-prompt hash the model never received

## Problem Statement

`Orchestrator.run_turn` rebuilds `system_prompt = build_system_prompt(profile)`
on every turn and writes its sha256 into `record.system_prompt_hash`. But
when a cached client is injected (the new common path), the prompt that the
model actually sees is the one frozen into `GeminiClient._config.system_instruction`
at session start. The prompt sent and the prompt hashed are no longer the
same artifact.

**Impact on ADR §7's "self-contained, replayable record" claim:** if the
data profile ever changes mid-session (live reload, manual DB tweak, new
seed), `record.system_prompt_hash` will attest to a fresh prompt the model
never received. The audit trail silently lies. The replay-by-snapshot
property of §10 (R10) is broken whenever this drift happens.

Today: dormant because `REFERENCE_TODAY` is a hard-coded constant and the
seed doesn't change. Latent the moment any of that becomes dynamic — and
the cost of fixing it now is small.

## Findings

- `agent/orchestrator.py:152-164` — per-turn `system_prompt` rebuild and hash.
- `agent/llm_gemini.py:55-62` — `_config.system_instruction` frozen at construction.
- `agent/orchestrator.py:112-128` (`build_session_client`) — point at which the prompt is captured for the session.
- ADR §7 explicitly promises a self-contained record; this defect violates it.

## Proposed Solutions

### Option A: Expose the actually-sent prompt hash on `LLMClient`

Add a property to the protocol:

```python
class LLMClient(Protocol):
    @property
    def system_prompt_hash(self) -> str: ...
```

`GeminiClient` returns the hash computed at construction; `FakeLLMClient`
returns a test-supplied value. `run_turn` reads it from the client instead
of recomputing.

- **Pros:** The audit log now reflects the prompt the model truly saw, by
  construction. Honest to the protocol. Easy to extend to other providers.
- **Cons:** Slight protocol broadening; one tiny test fixture update.
- **Effort:** Small.
- **Risk:** Low.

### Option B: Hash at `build_session_client` time, pass into orchestrator

Compute the hash inside `build_session_client`, store on a wrapper
dataclass `LLMSession(client, system_prompt_hash)`. Orchestrator records
the wrapper's hash.

- **Pros:** No protocol change.
- **Cons:** Introduces a new wrapper type for one field. Awkward asymmetry
  with the test path (no wrapper) unless tests also create one.
- **Effort:** Small.
- **Risk:** Low.

### Option C: Drop the per-turn rebuild; record only when the prompt changes

Stop recomputing `system_prompt` on every turn. Cache it on the
orchestrator at first run, record once, set the same hash on every record.

- **Pros:** Zero waste.
- **Cons:** Couples orchestrator to "the client owns the prompt" — works,
  but the orchestrator no longer has visibility into what was sent.
- **Effort:** Small.
- **Risk:** Low.

## Recommended Action

**Option A** — keeps the protocol honest and the audit log honest, single
small property add. Matches what the architecture-strategist suggested.

## Technical Details

- **Files:** `agent/llm.py` (Protocol), `agent/llm_gemini.py` (property),
  `agent/orchestrator.py` (read from client), `tests/test_orchestrator.py`
  (`FakeLLMClient` adds the property), `docs/ADR-001` §7 (no change needed
  if fixed).

## Acceptance Criteria

- [ ] `LLMClient` declares a `system_prompt_hash` property.
- [ ] `GeminiClient.system_prompt_hash` returns `sha256` of the
      `system_instruction` it was built with.
- [ ] `FakeLLMClient.system_prompt_hash` returns a fixed/scriptable value.
- [ ] `Orchestrator.run_turn` records `record.system_prompt_hash =
      client.system_prompt_hash` (no per-turn re-build).
- [ ] All tests pass; a new test asserts the recorded hash equals the
      client's hash for two consecutive turns sharing the same client.

## Work Log

- **2026-05-24** — Implemented Option A (Protocol property). Added
  `LLMClient.system_prompt_hash: str` attribute to the Protocol;
  `GeminiClient` computes it at construction; `FakeLLMClient` accepts an
  override (default `"fake-prompt-hash"`). Orchestrator's `run_turn` now
  reads `client.system_prompt_hash` and only rebuilds the system prompt
  in the no-injected-client fallback path. New test
  `test_record_uses_clients_system_prompt_hash` locks the invariant. 83/83
  tests pass.

## Resources

- Reviewers: `security-sentinel` P2-2, `architecture-strategist` P2,
  `agent-native-reviewer` finding #1.
