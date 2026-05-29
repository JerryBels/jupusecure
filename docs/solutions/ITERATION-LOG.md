# Iteration Log — Jupus Takehome (autonomous /loop)

This is the **compound ledger** for the 4-hourly autonomous loop. Every
iteration MUST read this whole file first, then append a new dated entry at the
bottom. It records what is done, what is verified, what is pending, and what the
next iteration should prioritise.

**Project goal:** a complete, strongly defensible Staff-Engineer takehome — a
secure code-execution layer POC + ADR + (script for a) video. Scope is frozen
to the plan: `docs/plans/2026-05-21-001-feat-secure-code-execution-layer-plan.md`.
**Do not add new features.** Enhance, harden, test, review, and verify.

---

## Current state (snapshot)

- **Code:** complete. All plan phases 0–7 are written and committed (`93dbd64`).
- **Tests:** 37 pure-logic tests pass; 6 Docker integration tests skip.
- **Blocked on:** the Docker daemon (see below) and an OpenAI API key.
- **Plan status:** still `active` — flip to `completed` only once the sandbox is
  verified end-to-end in a real container.

## Environment blockers (retry every iteration)

1. **Docker Desktop will not start in this automated session.** `open -a Docker`
   was issued repeatedly; no `com.docker.backend` process came up (likely no GUI
   login session). Every iteration should retry: `open -a Docker` then poll
   `docker ps` for a few minutes. The moment Docker is up, the pending
   verification below becomes the #1 priority.
2. **No OpenAI API key.** `.env` is not created. The agent loop is fully unit-
   tested with a fake client; a *live* LLM run (`app.py`, `scripts/cli.py`)
   cannot be verified without the user's key. Do not block on this — the user
   will supply the key. Note it as a user-action item, don't invent a key.

## Pending verification (do as soon as Docker is up)

1. `docker build -t jupus-sandbox sandbox/image` — build must succeed.
2. `python scripts/demo_guardrails.py` — all 7 guardrail cases must pass.
3. `python -m pytest` — all 43 tests (incl. the 6 integration tests) must pass.
4. Manually run one happy-path snippet through `Runner.run()` and confirm the
   envelope round-trips.
5. If anything fails: fix it, re-run, record the fix here. Likely suspects:
   - macOS Docker Desktop file-sharing for the `.sandbox_runs/` bind mount
     (it is under `/Users`, so it should be allowed — verify).
   - `tmpfs` `noexec` interfering with nothing (Python runs from the root fs;
     `/tmp` only holds the data file `child_out.json`).
   - `pids_limit=16` being too tight for entrypoint + child + threads — bump
     slightly if the happy path is starved (it should not be).
   - cgroup-v2 `OOMKilled` flag reliability on Docker Desktop.

## Known weaknesses a hostile reviewer WILL attack (work these in later passes)

- **DRY:** the sentinel string `__JUPUS_SANDBOX_ENVELOPE__` is duplicated in
  `sandbox/image/entrypoint.py`, `sandbox/runner.py`, and
  `scripts/demo_guardrails.py`. `RESULT_CAP_BYTES` is in both `config.py` and
  `entrypoint.py` (the image file can't import `config`, so some duplication is
  unavoidable — but document/justify it, e.g. a comment cross-referencing).
- **Classification heuristics** (`_looks_like_oom`, `_looks_like_pid_exhaustion`)
  are stderr string-matching — fragile by nature. The ADR already calls
  classification "best-effort"; consider tightening or adding tests around it.
- **No true token streaming** — the UI word-streams a completed answer. Defended
  in the ADR; fine, but be ready to defend it.
- **`peak_memory` is not captured** — acknowledged gap. Q3 of the video asks
  exactly this ("what observability would you need that your POC doesn't have").
- **`Orchestrator._run_tool_calls`** is on the longer side — review for clarity;
  the terminal-decision dict is slightly awkward. Consider a small dataclass.
- **`entrypoint.py` parent process** — confirm it truly always emits an envelope
  even when the child is OOM-killed mid-run (the host catches OOM independently,
  but trace it).

## Suggested iteration plan (each ~4h pass picks ONE focus)

- **Iter 2:** retry Docker; if up, do all Pending Verification + fix. If still
  down, do a critical line-by-line review of `sandbox/` (the security core) as a
  hostile reviewer; fix what is right.
- **Iter 3:** critical review of `agent/` (orchestrator/prompts) + add edge-case
  tests (malformed envelopes, unicode in results, empty caseload).
- **Iter 4:** DRY/KISS/SOLID pass — centralise the sentinel & shared constants
  where possible; tidy `_run_tool_calls`.
- **Iter 5:** write `docs/video-script.md` — the leadership pitch + the 3 deep-
  dive answers mapped to the narrative anchors in the plan. (Personal prep doc.)
- **Iter 6+:** more guardrail edge cases (symlink escape attempts, `/proc`
  probing, env-var leakage checks), observability tests, ADR polish, and a final
  "hostile manager" review pass over the whole repo.
- Always: re-run `python -m pytest` and keep it green; commit each pass.

## Done / verified WITHOUT Docker (iteration 1)

- Data layer: 20 cases seed correctly; profile excludes raw client names; the
  four example queries have verified answers (Q1-2026 = 1,085,000;
  Q1-2025 = 833,000; provision #4821 = 13,500).
- `child_runner.py` result contract: ok / fallback vars / no_result /
  syntax_error / runtime_error / result_type hint / stdout capture / SystemExit
  caught — all verified by running it directly.
- `Runner._classify` + `_parse_envelope`: precedence and the envelope-forgery
  defense verified by unit tests.
- Orchestrator: prose route, code route, retry-then-succeed, retry-exhausted,
  anti-oscillation early stop, infra-error no-retry, malformed-args handling,
  post-hoc unrouted-computation guard — all verified with a fake LLM client.

---

## Iteration entries

### Iteration 1 — 2026-05-21 ~20:00 (build)

Built the entire POC from the plan: scaffold, the hardened sandbox (Dockerfile +
trusted parent `entrypoint.py` + `child_runner.py`), the host `Runner`, the mock
data layer, the agent orchestrator, observability, the Streamlit app, the CLI
harness, the deterministic guardrail demo, the test suite, the ADR, and the
README. Committed as `93dbd64`. 37/37 logic tests green. Docker would not start
in this session, so container verification is deferred to the next iteration.
Next: get Docker up and run the Pending Verification list.

### Iteration 2 — 2026-05-21 (hostile-manager review of the sandbox core)

Docker still would not start (retried). Did a critical security review of
`sandbox/` as a hostile reviewer. **Main finding:** the ADR's integrity claim
*overclaimed* — it said a snippet "cannot forge a success classification". It
can: the untrusted snippet shares a process with `child_runner.py`, so it can
write its own result file and `os._exit` before `child_runner` runs. The honest
analysis (now in ADR §4): a forged envelope grants the snippet nothing — it
still cannot reach the network, escape, or evade resource limits; the only
unforgeable signals are host-observed (timeout, OOM, exit code), and
`_classify` already ranks those above the envelope. **Fixed:**

- ADR §4 rewritten with a precise, honest integrity claim; residual-risk note
  and a new iteration-path item (snippet-as-grandchild-subprocess) added.
- `entrypoint.py` docstring corrected (no longer claims "cannot forge").
- `child_runner.py`: `compile()` now also catches `ValueError` (null bytes in
  source) — was an uncaught crash path.
- `runner._stage_inputs`: the shared `.sandbox_runs/` parent dir is now 0700 so
  staged client PII is not traversable by other host users.
- Renamed `test_envelope_forgery_is_contained` ->
  `..._via_stdout_is_contained` and made the demo case description precise
  (the stdout vector is contained; the file vector is documented, not hidden).
- Added 4 tests (null-byte source, OOM-overrides-forged-ok, 2x staging perms).
  41/41 logic tests green. Committed this iteration.

Next (Iter 3): critical review of `agent/` (orchestrator + prompts), add
edge-case tests (malformed envelopes, unicode results, empty caseload).
Still pending: Docker verification, then the DRY pass (sentinel constant) and
the video script.

### Iteration 3 — 2026-05-22 (hostile-manager review of the agent layer)

Docker still down (retried). Critically reviewed `agent/`. **Main finding —
a real streaming bug:** `_run_tool_calls` collected progress events into a
list and `run_turn` yielded them with `yield from terminal["events"]` *after*
the whole round (including `runner.run()`) had finished. So the "running in
sandbox" status was emitted only once the sandbox had already returned — the
UI could never show status *while* code executed, which the brief explicitly
grades. **Fixed:**

- `_run_tool_calls` is now a generator: it `yield`s the `code` and
  `running` events live (before `runner.run` is called) and `return`s the
  terminal decision; `run_turn` consumes it with `terminal = yield from ...`.
- `run_turn`'s main loop is wrapped in try/except — an OpenAI API exception
  no longer crashes the turn with a raw traceback; it becomes a graceful
  answer and a logged record. Added an `error` field to `TurnRecord`.
- Empty/whitespace model answer now falls back to a rephrase prompt instead
  of showing the user a blank message.
- Added 9 tests: streaming-order (status before `runner.run`), API-error
  handling, empty-answer fallback, and a new `tests/test_prompts.py`
  (empty-caseload prompt build, routing-guard regex). **50 logic tests pass.**

Reviewed but deliberately left as-is (defensible): the post-hoc routing guard
over-flags definitional answers that mention a number — intentional, the guard
is conservative-by-design and the badge wording stays factually true.

Next (Iter 4): DRY/KISS pass — centralise the `__JUPUS_SANDBOX_ENVELOPE__`
sentinel (duplicated in entrypoint/runner/tests) and shared caps; review
`_run_tool_calls`/`_classify` for clarity. Then Docker verification when up,
and the video script.

### Iteration 4 — 2026-05-22 (DRY / KISS pass)

Docker still down (retried). Focused cleanup pass:

- **Sentinel centralised.** `__JUPUS_SANDBOX_ENVELOPE__` was hard-coded in
  ~4 places. `sandbox/runner.py` now exposes a public
  `SANDBOX_ENVELOPE_SENTINEL`; `test_runner_classification.py`,
  `test_runner.py`, and `demo_guardrails.py` all derive from it (the forgery
  snippets are built with an f-string). The one remaining copy —
  `entrypoint.py` — is unavoidable (that file is built standalone into the
  image and cannot import the project) and now carries a sharp comment naming
  the canonical host-side definitions for the sentinel, `RESULT_CAP_BYTES`,
  and the container paths.
- **`_short_id()` helper** — `new_turn_id`/`new_execution_id` no longer each
  repeat the uuid slicing; they call one helper and document their intent.
- **`_run_tool_calls` clarity** — it returned a stringly-typed 3-key dict
  (`terminal["final_answer"]` etc.). Now returns a `_RoundOutcome` dataclass
  with an `ends_turn` property; `run_turn` reads `terminal.ends_turn`.
- 50 logic tests still pass; no behaviour change.

Reviewed and deliberately left alone: `_classify` (clear enough — early
returns, single concern); `_read_logs`'s `stdout: bool` param (readable);
the entrypoint↔config path/cap duplication (unavoidable, now documented).

Next (Iter 5): write `docs/video-script.md` — leadership pitch + the three
deep-dive answers mapped to the plan's narrative anchors. Docker verification
still pending whenever the daemon comes up.

### Iteration 5 — 2026-05-22 (video walkthrough runbook)

Docker still down (retried). Wrote `docs/video-script.md` — the recording
runbook for the required Loom walkthrough (plan R14). It is structured as
beats + on-screen actions, not a verbatim script, covering:

- the leadership pitch (problem in business terms, air-gap as the safety
  story, demo as proof, the ask);
- Q1 (sandbox security) — mapped to `_create_container` and `entrypoint.py`,
  with `demo_guardrails.py` as the live block; it explicitly tells the
  iteration-2 integrity-claim correction as the honest "what changed along
  the way" story;
- Q2 (scope/trade-offs) — mapped to the ADR deferred table; a structured
  answer to the "add JavaScript mid-sprint" prioritisation scenario;
- Q3 (failure handling) — a concrete failure prompt (wrong field name
  `claim_value` -> KeyError -> retry -> self-correct), the silently-wrong-
  calculation debugging walk via the turn record, and the honest list of
  missing observability (alerting, tracing, the verification layer).

The "[YOUR STORY]" sections are deliberately left as prompts with CV-grounded
anchors (MVNO untrusted input; solo-shipping Buddzy; a multi-provider AI
incident) — the specific incidents must be Jeremy's own, not invented.
README's docs line updated to mention the runbook. 50 tests still green
(no code touched).

Next (Iter 6+): Docker verification when the daemon is up (build image, run
demo_guardrails, run the 6 integration tests); otherwise continue hostile
review — guardrail edge cases (symlink/proc probing, env-var leakage),
observability tests, and a whole-repo final review pass.

### Iteration 6 — 2026-05-22 (hostile review: observability + data layers)

Docker still down (retried). Reviewed the two layers not yet examined
(`observability/`, `data/`). Three real bugs found and fixed:

- **SQLite connection leak.** `with sqlite3.connect(...)` commits but does
  NOT close the connection. `repository.all_cases()` is called twice per
  turn, so the long-running Streamlit process leaked a connection per call.
  Fixed with `contextlib.closing(...)` in `repository.all_cases()` and
  `seed.seed()`.
- **Logger writes were not concurrency-safe.** Streamlit runs each session
  in its own thread of one process; a turn record can exceed the
  pipe-atomic write size, so concurrent appends to the JSONL file could
  interleave. Added a process-wide `threading.Lock` around the append
  (module-level — a fresh `TurnLogger` is created per turn).
- **`read_all` crashed on one corrupt line.** Now skips corrupt/partial
  lines instead of failing the whole read.

Added `tests/test_logger.py` (4 tests: 24-thread concurrent logging stays
intact, corrupt-line tolerance, 0600 file mode, empty-log read). **54 logic
tests pass.**

Reviewed and found clean: `config.py`, `app.py`, `cli.py`,
`demo_guardrails.py`.

Next (Iter 7+): Docker verification when the daemon is up; otherwise a final
whole-repo review pass and, when Docker is available, extra guardrail
edge-case demos (symlink/`/proc` probing, env-var leakage).

### Iteration 7 — 2026-05-22 (whole-repo final review pass)

Docker still down (retried). Whole-repo review for dead code and consistency:

- **Removed 3 dead config constants** — `RESULT_CAP_BYTES`,
  `CONTAINER_SNIPPET_PATH`, `CONTAINER_CASES_PATH` were defined in `config.py`
  but read nowhere (verified by grep). The real result cap lives in
  `entrypoint.py` (which cannot import `config`); the container paths are
  owned by `entrypoint.py` and mirrored implicitly by `_stage_inputs`.
- **Tightened the now-stale comments**: `entrypoint.py`'s cross-reference
  comment (no longer points at removed config names) and a filename-coupling
  comment in `runner._stage_inputs`.
- **Completed observability**: `result_source` (which variable the snippet's
  answer came from — `result` vs the `answer`/`output`/single-binding
  fallback) was computed by `child_runner`, plumbed through `ExecutionResult`,
  then dropped. It is now logged on `ExecutionAttempt` — useful debugging
  signal, no longer plumbed-but-unused.
- 54 logic tests still pass.

**COMPLETION STATUS — read before iterating further.** All four layers have
had a hostile-review pass; deliverables are complete: POC code, README, ADR,
video runbook, 54-test suite (48 pure-logic + 6 Docker integration tests that
skip). The assignment is **substantively done**. The ONE outstanding item is
Docker verification — the daemon has not come up in any iteration.

Guidance for Iter 8+: (1) always retry Docker first — if it comes up, that is
the highest-value remaining work: `docker build -t jupus-sandbox
sandbox/image`, `python scripts/demo_guardrails.py`, `python -m pytest`, fix
anything that fails, record results here, flip the plan to `completed`.
(2) If Docker stays down, do NOT manufacture work or expand scope — a short
iteration that re-verifies tests are green and reports "no change needed,
awaiting Docker" is the correct, disciplined outcome. Only make a change if a
genuine issue is found.

### Iteration 8 — 2026-05-22 (test coverage for the trusted parent)

Docker still down (retried, longer wait). One genuine gap remained:
`sandbox/image/entrypoint.py` — the trusted parent process, the most
security-critical file — had **zero direct test coverage**; it was exercised
only by the 6 skipped Docker integration tests, because its `main()`
hard-coded the in-container paths.

- Parameterised `entrypoint.main()` — paths are now arguments defaulting to
  the in-container constants. Behaviour-preserving: the image's `__main__`
  still calls `main()` with the defaults.
- Added `tests/test_entrypoint.py` (6 tests) — the full parent->child contract
  flow now runs on the host without Docker: happy path, runtime error, syntax
  error, hard-exit (`os._exit`) -> `no_result`, oversized-result capping, and
  **the envelope-forgery-via-stdout containment** — that security claim is now
  verified without Docker, not just asserted.
- **60 logic tests pass** (was 54).

This was real work (a security-critical file with no coverage), not invented
churn. After this pass the well of genuine non-Docker work is essentially
dry: all four layers reviewed, 60 tests across every layer including the
trusted parent, ADR verified accurate, dead code removed, DRY/KISS done.

Iter 9+: retry Docker; if up, run the full verification. If still down, a
brief "re-verified green, no change needed" pass is the correct outcome —
do not invent changes.

### Iteration 9 — 2026-05-23 (verification-only pass)

Docker still down (retried with the longer wait). 60/60 logic tests still
pass. Additionally verified two README setup steps work from a fresh root:

- `python -m data.seed` -> `Seeded 20 cases into data/cases.db`. (The
  reviewer's first command after install — confirmed correct.)
- `app.py` parses and its import spec builds cleanly (full Streamlit run is
  interactive and can't be smoke-tested headlessly, but the file is valid).

No code changes — the codebase is in a state I'd defend as-is. Iter 10+:
same protocol — retry Docker, otherwise re-verify and report. Resist the
urge to invent changes; restraint is the right answer the brief rewards.

### Iteration 10 — 2026-05-23 (real defect found and fixed)

Docker still down (retried). Followed the "verification-only" protocol from
iter 9 — but extended it by actually *running* the scripts the way a fresh
reviewer would (instead of just verifying their imports parsed). That
surfaced a real, user-visible defect that iter 9's check missed:

- **`Runner.__init__` raised when Docker was down.** `docker.from_env()`
  connects eagerly to auto-detect the API version, so both `cli.py` and
  `demo_guardrails.py` crashed with a ~100-line traceback before the
  `runner.ping()` check could run. A reviewer cloning the repo without
  Docker would have hit this immediately.
- **Fix:** the constructor now catches `DockerException` and leaves
  `_client = None`; `ping()`, `reap_orphans()`, and `run()` all short-circuit
  when there's no client (`run()` returns a clean `infra_error`).
- Both scripts now print `Docker is not available -- start Docker Desktop
  and retry.` on stderr and exit cleanly.
- Added 4 regression tests (`tests/test_runner_staging.py` Docker-down
  section). **64 logic tests pass** (was 60).

**Honest correction to the iter 9 entry:** "verified the README setup works"
was overstated — I verified the commands *parse* and `python -m data.seed`
runs, but I did not actually execute `python scripts/cli.py` or
`scripts/demo_guardrails.py` as a fresh user would. That gap is exactly how
the bug slipped through. Recording it so future iterations remember: the
right verification of a script is to RUN IT, not to compile it.

Iter 11+: same protocol — retry Docker, otherwise re-verify by actually
running the scripts (not just compiling them).

### Iteration 11 — 2026-05-23 (run-it-for-real verification of app.py)

Docker still down (retried). Applied iter 10's "actually run the entry points"
principle to the one script it hadn't been applied to: `app.py`. Started the
Streamlit server headlessly (`streamlit run app.py --server.headless=true
--server.port=8765`), waited for it to boot, and probed it twice:

- Uvicorn started on :8765, no exception in the boot log.
- `HTTP GET /` -> 200, returned the standard Streamlit shell.
- A second probe after first render -> 200, 5381 bytes of HTML.

That confirms `_components()` no longer crashes when Docker is down (iter 10
fix held end-to-end for the Streamlit entry point too) and the chat shell
loads cleanly. A reviewer running `streamlit run app.py` will get a working
UI; errors only surface on submitting a query, where the no-API-key /
no-Docker paths produce friendly `st.error()` messages, not stack traces.

No code changes — the defect surface is now covered for all three entry
points (cli, demo, app). 64 logic tests still green.

Iter 12+: same protocol; the well of genuine non-Docker work is essentially
empty. Retry Docker, otherwise a one-line "re-verified green" entry is the
correct outcome.

### Iteration 12 — 2026-05-23 (cover the no-API-key Orchestrator path)

Docker still down (retried). Applied iter 10's "what do I claim works but
tests don't exercise" lens one more time: the Orchestrator's
`_default_client()` raises `OrchestratorError` when `OPENAI_API_KEY` is
unset -- the message a reviewer running the chat without `.env` sees -- but
every existing `test_orchestrator.py` test injects a fake `client=`, so
that path is never executed by the suite. Same shape as the iter-10
Runner defect: claimed working, but untested.

Added one regression test (`test_orchestrator_raises_when_no_api_key_is_set`)
that monkeypatches `config.OPENAI_API_KEY = ""` and constructs `Orchestrator`
without injecting a client; it must raise `OrchestratorError` mentioning
`OPENAI_API_KEY`. **65 logic tests pass** (was 64). No production-code
changes.

Iter 13+: every claimed prerequisite-failure path is now actually exercised
(Docker-down for Runner; no-key for Orchestrator). Retry Docker, otherwise
the well is empty -- a one-line "re-verified green" is the correct entry.

### Iteration 13 — 2026-05-23 (re-verified green, no change)

Docker retried, still down. 65/65 logic tests pass. No change — exactly the
disciplined outcome the ledger calls for.

### Iteration 14 — 2026-05-24 (Code review fixes)

Addressed findings from code review using `code_reviewer` subagent:
- Fixed resource-exhaustion telemetry classification: `Runner._classify` now compiles `blob` from `envelope.get("error")`, `envelope.get("traceback")`, `envelope.get("stderr")`, and host-level `stderr`. This ensures C-level allocators panics, SIGKILLs, or early OOM/PID failures printing to stderr (e.g. producing `"no_result"`) are correctly mapped to `BUCKET_RESOURCE_EXCEEDED` instead of `BUCKET_RETRYABLE_CODE_ERROR` or `BUCKET_INFRA_ERROR`.
- Added Docker client session cleanup: Added `Runner.close()` and `__del__()` destructor to close the underlying `docker.from_env()` client session cleanly.
- Added regression tests: Added `test_resource_exhaustion_in_stderr_reclassified` in `test_runner_classification.py` and `test_close_cleans_up_client` in `test_runner_staging.py`.
- 67/67 logic tests pass.

### Iteration 15 — 2026-05-24 (LLM provider swap: OpenAI → Gemini, with proper isolation)

User is back. Two big things happened in this iteration:

**1. Docker is up and the sandbox is verified end-to-end.** The user started
Docker Desktop and built the image while away. All 6 previously-skipped
container integration tests now pass; `scripts/demo_guardrails.py` reports
**7/7 guardrails firing in real containers** (network blocked by `--network
none` -> DNS resolution failure; filesystem write -> read-only-fs OSError;
infinite loop killed at 15s; memory bomb OOM-killed; thread bomb contained
to timeout; envelope-forgery contained). The integrity claim is now PROVEN,
not just unit-tested. The long-standing "pending Docker verification" item
is closed.

**2. Swapped the LLM provider from OpenAI to Gemini.** User asked, and
correctly pushed back on my initial "direct swap (two files) is simpler"
framing -- a swap that touches two files means the abstraction was leaky.
Did it properly:

- New `agent/llm.py` -- a provider-neutral `LLMClient` Protocol with two
  methods (`send_user(text)`, `send_tool_results(results)`) plus neutral
  dataclasses (`ToolDefinition`, `ToolCall`, `ToolCallResult`, `LLMResponse`).
- New `agent/llm_gemini.py` -- **the only file that imports `google.genai`**.
  Holds the conversation state internally.
- `agent/tools.py` now declares the tool as a neutral `ToolDefinition`
  (JSON Schema parameters only). Each client adapts to its envelope.
- `agent/orchestrator.py` refactored to depend ONLY on `LLMClient`. Lost all
  OpenAI-specific code (`response.output` iteration, `function_call_output`
  construction, `responses.create`, input-list plumbing). The retry/routing
  /fallback/post-hoc-guard/error-handling logic is unchanged.
- `agent/prompts.py` was already provider-agnostic; **zero changes**.
- `config.py` + `.env.example`: `OPENAI_*` -> `GEMINI_*`; default model is
  `gemini-2.5-flash-lite` (cheapest 2.5 with function calling).
- `requirements.txt`: dropped `openai`, added `google-genai>=0.7,<2`.
- `tests/test_orchestrator.py`: `FakeClient` (OpenAI-shape) became
  `FakeLLMClient` implementing the same `LLMClient` protocol as production.
  Tests still call ZERO real APIs.
- ADR §5 rewritten to describe the provider-agnostic design as the
  architectural decision; Alternatives Considered names OpenAI as a
  considered choice and YAGNI as the reason against a multi-provider runtime.
- README updated (Gemini key, free-tier link). Video script Q1 gets a new
  "another evolution" beat naming the LLMClient pattern explicitly.

**73 logic tests pass** (the 6 Docker integration tests run now, not skipped).
Adding a new provider in future = one new `LLMClient` implementation file +
swap one factory line. That's the design the ADR claims and now the code
actually does.
