# Jupus Code Review Plan — by Bounded Context

A self-review walkthrough of the whole codebase, organised around the four
DDD bounded contexts. Contexts are ordered **bottom-up by dependency** — each
context's collaborators are already understood by the time you reach the one
that depends on them, so the `Orchestrator` (which touches everything) comes
last. Within each context, files are in reading order: domain types first,
then the logic that uses them.

Two anti-corruption layers (the `LLMClient` port, the sandbox envelope) are
called out where they sit.

---

## Context 1 — Caseload `data/`  *(start here: depends on nothing)*

**Owns:** the legal data model, what's queryable, and what shape of data the
rest of the system is allowed to see. The air-gap's data side lives here.

**Read in order:**
1. `data/case.py` — `Case`, `CaseField`, `EXAMPLE_CLIENT_NAME`. The domain entities.
2. `data/seed.py` — `CASE_FIELDS` (single schema source of truth), seed rows, `seed()` / `ensure_seeded()`.
3. `data/profile.py` — `DataProfile`, `DateRange`, `render_for_llm()`, the `__post_init__` air-gap guard.
4. `data/repository.py` — `CaseRepository`: the read seam, `all_cases()` / `cases_json()` / `data_profile()`.

**Review lens:**
- Is `CaseField` truly the *single* source of schema truth? Does anything else hardcode field names?
- `cases_json()` (raw, sandbox-only) vs `data_profile()` (shape-only, LLM-facing) — is the separation airtight? Could a raw field value reach `render_for_llm()`?
- Does the `__post_init__` guard cover every path that builds a `DataProfile`?
- Does `cases_json()`'s `Case → dict` projection match exactly what the prompt promises the LLM?

**Seam exposed:** `DataProfile.render_for_llm()` — the only thing Conversation consumes. **Tests:** `test_data.py`.

---

## Context 2 — Computation `sandbox/`  *(self-contained: the security boundary)*

**Owns:** containment, the result contract, outcome classification, resource
limits, the in-container envelope protocol.

**Read in order:**
1. `sandbox/image/constants.py` — the **wire-protocol ACL**: `SANDBOX_ENVELOPE_SENTINEL`, `ChildResult`, `SandboxEnvelope`.
2. `sandbox/image/Dockerfile` — the image contract (what's baked in, the hardening).
3. `sandbox/image/child_runner.py` — the **untrusted** child process: runs the snippet, resolves the result, writes the result doc.
4. `sandbox/image/entrypoint.py` — the **trusted parent** (PID 1): spawns the child, owns real stdout, emits the one envelope.
5. `sandbox/runner.py` — host-side `Runner`: container lifecycle, `_classify`, the trust decision. *(the big one)*

**Review lens:**
- The trust boundary: host-observed signals (exit code, OOM, timeout) vs. envelope `status`/`result`. Does `_classify` rank them correctly?
- The envelope-forgery defense: trace a snippet `print()`-ing a fake sentinel line — where does it land?
- `ExecutionRequest.__post_init__` id validation — does it actually block `../` traversal in `run_dir` and the container name?
- The two boundaries the wire dataclasses cross (child→parent file, container→host stdout) — is each `to_dict()`/`from_dict()` lossless?
- Does every resource limit in `_create_container` have a guardrail test behind it?

**Seam exposed:** `Runner.run(ExecutionRequest) → ExecutionResult` + the `BUCKET_*` constants. **Tests:** `test_runner.py` (Docker), `test_runner_classification.py`, `test_runner_staging.py`, `test_child_runner.py`, `test_entrypoint.py`.

---

## Context 3 — Observability `observability/`  *(near-leaf: one import from Conversation)*

**Owns:** what's recorded per turn, the replay format, audit retention, the
PII-surface posture.

**Read in order:**
1. `observability/records.py` — `TurnRecord`, `ExecutionAttempt`, the id factories, `NON_OK_OUTCOMES`.
2. `observability/logger.py` — `TurnLogger`: thread-safe append, the `0600` PII guard, tolerant read.

**Review lens:**
- Is a `TurnRecord` actually self-contained / replayable? (It carries the full system prompt now — verify nothing else is needed to re-run a turn.)
- `ExecutionAttempt` mirrors ~10 fields of `ExecutionResult` — acceptable, or a drift risk? *(known 🟢 smell from the DDD audit.)*
- Does the `0600` + git-ignore posture match what the ADR claims?
- Thread-safety of the append under Streamlit's per-session threads.

**Seam exposed:** `TurnLogger.log(TurnRecord)`. **Tests:** `test_logger.py`.

---

## Context 4 — Conversation `agent/`  *(read last: depends on all three above)*

**Owns:** routing policy (prose vs code), retry policy, conversation memory,
the prose-vs-code decision.

**Read in order:**
1. `agent/llm.py` — the **LLMClient ACL port**: Protocol, `TokenUsage`, `ToolDefinition/Call/Result`, `LLMResponse`, the error taxonomy.
2. `agent/tools.py` — `EXECUTE_PYTHON_TOOL`: the agent's single affordance contract.
3. `agent/prompts.py` — `build_system_prompt`, the routing policy, `retry_instruction`, the `looks_like_unrouted_computation` post-hoc guard.
4. `agent/llm_gemini.py` — `GeminiClient`: the **ACL adapter** (only file importing `google.genai`).
5. `agent/orchestrator.py` — `Orchestrator`: the loop, `ToolArguments`, the `TurnEvent` union, `build_session_client`. *(the other big one)*

**Review lens:**
- Is the orchestrator truly provider-agnostic? Grep it for any Gemini-shaped concept — there should be none.
- The `TurnEvent` union — do both consumers (`app.py`, `cli.py`) handle every member?
- Retry policy: bounded-2, identical-failure short-circuit, honest fallback — read `_execute_tool_calls` and `_RoundOutcome`.
- **Known 🟡 leak:** `orchestrator.py` imports `BUCKET_OK` / `BUCKET_INFRA_ERROR` from Computation to drive retry logic — the one boundary violation the DDD audit flagged (ADR iteration path #8). Confirm whether it still bothers you or is acceptable for beta.

**Seams consumed:** `DataProfile.render_for_llm()` (Caseload), `Runner.run()` + `BUCKET_*` (Computation), `TurnLogger.log()` (Observability). **Tests:** `test_prompts.py`, `test_llm_gemini.py`, `test_orchestrator.py`.

---

## Context 5 — Composition roots & cross-context seams  *(the wiring)*

**Read:**
1. `config.py` — every limit and path in one place.
2. `app.py` — the Streamlit composition root + UI (the `@st.cache_resource` singleton).
3. `scripts/cli.py` — the CLI composition root.
4. `scripts/demo_guardrails.py` — the deterministic, LLM-free security demo.
5. `scripts/show_log.py` — the log inspector.

**Seam audit (the DDD payoff)** — confirm each crossing is clean:

| From → To | Shape | Verdict to confirm |
|---|---|---|
| Conversation → Computation | imports `BUCKET_*` constants | 🟡 the known vocabulary leak |
| Conversation → Caseload | calls `render_for_llm()` | ✅ clean port |
| Conversation → Observability | builds `TurnRecord` | ✅ |
| Observability → Conversation | imports `TokenUsage` | 🟢 minor, acceptable |
| Computation ↔ host | `SandboxEnvelope` wire | ✅ ACL |

- Confirm the three composition roots (app / cli / tests) wire the *same* injectable graph differently — the proof the boundaries hold.

---

## Suggested cadence

Five sittings, one per context, ~45–60 min each if you read the tests
alongside. **Context 2 (Computation)** and **Context 4 (Conversation)** are the
two heavy ones — give them the most time. The other three are quick.
