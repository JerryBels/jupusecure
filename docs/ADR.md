# ADR: Secure Code-Execution Layer for the AI Chat

- **Status:** Accepted (beta prototype)
- **Date:** 2026-05-21
- **Author:** Jeremy Belolo
- **Supersedes / Superseded by:** —

An internal decision record for the engineering team. It explains the problem,
the design we chose for the private-beta prototype, what we traded away, and
what we are deliberately deferring.

---

## Context

Our AI chat lets lawyers query their caseload in natural language. The LLM is
strong at prose but unreliable at **deterministic computation** — date
arithmetic ("three months ago"), decimal maths (provisions), and aggregations.
It produces confident, wrong numbers, which erode trust fast in a legal
product. The brief's own examples ("total claim value filed in Q1 vs last
year", "provision for case #4821") are the queries failing today.

The mandate: ship a server-side code-execution pathway the chat agent can
invoke when language generation won't produce a reliable answer — fast enough
for a private beta, with a high code-quality bar and **privacy protected from
day one**. Delivered largely solo.

**Constraints shaping this ADR:**

- Beta quality, not production. Correctness and security must be real; scale,
  ops tooling, and polish are intentionally thin.
- The existing product is Django + PostgreSQL/pgvector. The POC uses Streamlit
  (per the brief — borrow chat scaffolding, invest effort in the sandbox) and a
  mocked SQLite caseload standing in for retrieval.
- The code we execute is **LLM-generated** — untrusted, but not (yet)
  adversarial-human. That distinction calibrates the threat model throughout.

---

## Decision

Four execution layers recur below, and the terms are used precisely:

```
HOST MACHINE      runner.py · orchestrator · Docker daemon · GEMINI_API_KEY · logs/
└── CONTAINER     network=none · read-only FS · dropped caps · 256 MB   (Decisions 2–3)
    ├── PARENT    entrypoint.py — PID 1, trusted                        (Decision 4)
    └── CHILD     child_runner.py + the untrusted snippet               (Decision 4)
```

The **container boundary** separates the container from the host; the **process
boundary** (parent/child) sits one level deeper, *inside* the container. A
container *escape* breaks the outer boundary and reaches the host; the result
contract defends the inner one. When this document says "the host," it means the
top layer — never the in-container parent.

### 1. The core loop

```
user query → agent (Gemini, tool calling, via LLMClient protocol)
           → either answer in prose
           → or call execute_python(code, purpose)
                → orchestrator runs code in a hardened, ephemeral container
                → result captured → fed back → agent weaves the final answer
```

Two bounded contexts do the core work — **Conversation** (`agent/`:
orchestrator, prompts, tool) and **Computation** (`sandbox/`: runner +
in-container image) — over a **Caseload** supporting context (`data/`: mock
caseload + profile) and a cross-cutting observability concern
(`observability/`: per-turn records). Keeping the Conversation↔Computation
boundary thin is an ongoing concern; the production roadmap tightens it
(Iteration Path, step 6). All three
entry points exercise the **same `Runner.run()` boundary**: `app.py` (Streamlit)
and
`scripts/cli.py` reach it through the orchestrator; `scripts/demo_guardrails.py`
calls it directly, to exercise the boundary in isolation without the LLM.

### 2. Sandbox isolation — hardened Docker, two-tier

Each execution runs in a **fresh, ephemeral, hardened Docker container**
destroyed immediately after. The standard hardening is table stakes — dropped
capabilities, `no-new-privileges`, a non-root user, a read-only root
filesystem, and Docker's default seccomp profile (exact flags in
`sandbox/runner.py`). Three choices are worth recording, because they are
decisions rather than defaults:

- **Resource ceilings are real ceilings, mapped to specific failure modes.**
  Memory is capped at 256 MB with **swap disabled** (`memswap_limit ==
  mem_limit`), so a memory bomb is *killed*, not slowed; `pids_limit=16` stops a
  fork bomb; an 8 MB file-size limit and a 64 MB `/tmp` cap disk use. A soft
  limit a snippet can grow past is not a limit.
- **`/tmp` is the only writable path, and it is `noexec`.** A snippet can stage
  data there but cannot write an executable and run it — closing the
  write-a-binary-then-run-it path, even though the air-gap already removes the
  download half of it.
- **The timeout is host-owned, not in-container** (15 s wall-clock; the host
  kills the container). Untrusted code cannot be trusted to enforce its own
  timeout, so the one signal that stops an infinite loop lives outside
  everything the snippet controls. This is the same principle as the result
  contract: the trustworthy signals are the host-observed ones.

The network namespace is removed entirely (`network_mode="none"`) — but that is
the **air-gap**, the primary control, and gets its own section next, not a
bullet here.

This is **two-tier by design**: hardened Docker locally for the POC; **AWS
Lambda in production**. The migration is a
`Runner` swap (`docker-py` → `boto3.client("lambda").invoke()` against the same
sandbox image deployed to ECR); the air-gap, the result contract, the
orchestrator, and the tests do not change. **Self-hosted gVisor (`runsc`) is the
alternative path** when Lambda is unsuitable — a customer requiring the sandbox
in their own VPC, or strict no-AWS sovereignty — and is a `runtime=` swap on the
same `Runner`. Either choice survives the architecture intact. (Migration
mechanics: Iteration Path, step 1. Why each tech: Alternatives.)

### 3. The security model is the air-gap, not the container

The primary control is **architectural, not a container flag**: the sandbox
holds **no credentials and no network**. Raw case data is injected only into the
sandbox as a read-only file; it never enters the LLM prompt. The LLM sees only a
**data profile** — schema, enum values, date ranges, null counts, and a
*synthetic* example row (`data/repository.py`).

Consequences of the air-gap:

- **Exfiltration is structurally impossible from the sandbox** — with no network
  namespace there is no socket to open, so the "POST the data to attacker.com"
  class of attack cannot even be attempted.
- **Prompt injection via case data is defused** — a hostile value in a
  `client_name` field cannot steer generated code, because raw field values
  never reach the model.
- A container **escape** — a kernel-level break out of a hardened,
  capability-dropped, seccomp-filtered container — is a high bar and genuinely
  rare. But it is the serious case, and the air-gap is not what saves you there.
  The no-network, no-credentials property belongs to the *container*;
  the host an escape reaches is the orchestrator's own machine, which in the POC
  has internet egress and holds a credential (`GEMINI_API_KEY`, plus the Docker
  socket). An escape could therefore exfiltrate. What keeps that hard is the
  container hardening, and the production move to Lambda's Firecracker microVMs
  shrinks the escape surface further. Stripping egress and secrets from the
  executor host itself — a separate segment, or Lambda with a scoped role — is a
  production step, covered under Orchestrator privilege below.

### 4. The result contract — what the process boundary can and can't guarantee

The result computed inside the container is **not** trustworthy, and it doesn't
need to be. "Exit code 0" doesn't mean "right answer," and untrusted code mustn't
be able to forge a success. So the trust boundary inside the container is a
**process boundary**:

- `entrypoint.py` is the **trusted parent** (PID 1). It never `exec()`s the
  snippet; it spawns `child_runner.py` in a **separate child process**.
- `child_runner.py` runs the untrusted snippet, resolves whatever it computed
  (`result`, with a forgiving fallback to `answer`/`output`/a single new
  binding), and writes a structured result document to the tmpfs.
- The parent reads that document, size-caps it, and emits **one** canonical
  envelope on the container's real stdout — a channel the child never holds a
  handle to. A snippet that prints a forged `__JUPUS_SANDBOX_ENVELOPE__` line
  only writes the *child's* captured stdout; it lands *inside* the real
  envelope's `stdout` field and is never mistaken for the envelope
  (`test_runner.py::test_envelope_forgery_via_stdout_is_contained`).
- The host `Runner` classifies the run from the **container exit state** (exit
  code, OOM-killed flag, whether it had to kill it) plus the envelope.

**The precise integrity claim — and it is easy to overclaim.** Two distinct
classes of signal, with different trust:

- **Host-observed outcomes are not forgeable.** Whether a run timed out, was
  OOM-killed, or how the container exited is decided by the host `Runner` from
  the Docker daemon — outside every process the snippet controls.
  `Runner._classify` ranks these host signals *above* the envelope, so a forged
  `status: "ok"` loses to a host-observed timeout or OOM kill
  (`test_runner_classification.py`). A snippet cannot loop forever or exhaust
  memory and report success: the container is killed first.
- **The envelope's `status` and `result` are NOT integrity-guaranteed.** They
  come from `child_runner.py`, which shares a process with the snippet — and
  code in a process can write any descriptor and read any memory that process
  holds. A snippet can write its own result file and `os._exit` before
  `child_runner` runs, forging `status: "ok"` with any value. This is
  unavoidable, and **acceptable**: a forged envelope grants the snippet *nothing
  it did not already have*. Its only power is to choose a result value — which
  is exactly its legitimate job. It still cannot reach the network, escape the
  container, or evade a resource limit. The worst case is a wrong *value* — the
  "silently wrong value" residual risk (see Residual Risks), not an escalation.

So the parent/child split is **not** what makes the result trustworthy — nothing
can. It buys two concrete things: (1) **crash isolation** — if the snippet
segfaults or `os._exit()`s, the parent survives and still emits a structured
envelope, so the host never sees an unexplained dead container; (2) **a clean
stdout channel** — the snippet's `print()` output can never be mistaken for the
envelope. A stronger design — running the snippet as a *grandchild* subprocess
so its **exit code** drives classification — is in the Iteration Path; it
narrows crash-hiding but still cannot make a cooperatively-chosen value
trustworthy.

`Decimal` is serialized as a string by a custom encoder — money must never
round-trip through `float`. This ceremony is **inherent to the Python-sandbox +
JSON-injection architecture**, not to SQLite: JSON has no arbitrary-precision
number type, so even with Postgres `NUMERIC` as the source (psycopg returns
`Decimal` natively) we'd still serialize amounts as strings to cross the
orchestrator → sandbox boundary. Only direct SQL execution (no sandbox JSON
injection) would remove it — a different architecture (see Alternatives).

### 5. Agent integration — provider-agnostic loop, Gemini today

- The orchestrator is **provider-agnostic**. It depends only on the `LLMClient`
  protocol (`agent/llm.py`) — a stateful conversation with `send_user(text)` and
  `send_tool_results(results)`. It never touches provider-specific shapes (input
  lists, content parts, function_call_output envelopes).
- The shipping provider is **Gemini** (`gemini-2.5-flash-lite`,
  `agent/llm_gemini.py` — the **only file** that imports `google.genai`). Adding
  a provider is a one-file change: a sibling implementing `LLMClient`, plus one
  factory line in `orchestrator._default_client_factory`. Tests don't change —
  `FakeLLMClient` already implements the protocol. Gemini was chosen for the POC
  because its free tier removes setup friction for a reviewer reproducing the
  demo, and the author runs Gemini in production (Buddzy) so the model-behaviour
  story is authentic; the choice is reversible by the abstraction above.
- The single tool is a neutral `ToolDefinition` in `agent/tools.py` (JSON Schema
  parameters); each provider client wraps it for its envelope.
- The prose-vs-code decision is **not** left to the model's instinct. The system
  prompt (`agent/prompts.py`) carries a hard, enumerated routing policy plus
  few-shot examples: any counting, date arithmetic, money maths, filtering, or
  grouping **must** call the tool.
- Bounded retry: at most **2 execution rounds** per turn
  (`MAX_TOOL_ITERATIONS`). Failures are fed back with a stripped traceback so
  the model self-corrects; identical code failing identically stops early;
  resource failures get an explicit "change the approach" instruction. On
  exhaustion the agent falls back honestly rather than guessing (see *Streaming & UX*).

### 6. Failure taxonomy — 5 buckets

The runner classifies every run into one of five buckets: `ok`,
`retryable_code_error`, `resource_exceeded`, `infra_error`, `retry_exhausted`
(the last owned by the orchestrator). A finer 9-class taxonomy was designed; we
**deliberately collapsed it to 5** — a 9-way table needs 9 branches and 9 tests
for distinctions that are not cleanly separable in practice. OOM / timeout /
PID-limit cannot be told apart reliably (all can surface as exit 137 / SIGKILL);
classification uses an explicit precedence (OOM flag → timeout flag → exit 137 →
stderr heuristics) and is documented as **best-effort**. The guardrail demo
asserts on the *bucket*, never an exact sub-reason, so it is not flaky by
construction.

### 7. Streaming & UX — status that's real, failure that's never silent

The brief asks how we keep the user informed while code runs, and what happens
when it fails. The orchestrator's `run_turn` is a **generator that yields typed
progress events live** as the turn unfolds; the Streamlit UI (`app.py`) renders
them inside an `st.status` block:

- **Live status**: `StatusEvent` stages — *drafting → running → retrying* —
  surface as the turn moves, so the user sees "Running in the secure sandbox…"
  rather than a frozen spinner. The drafted code is shown (`CodeEvent`) *before*
  the sandbox runs it.
- **Answer reveal is cosmetic, and labelled as such.** Once the answer is fully
  computed, the UI reveals it word-by-word (`_stream_words`) as a smoothing
  animation — **not** token streaming from the model. The honest, load-bearing
  part is the live *status*, which reflects real backend state; real token
  streaming through the tool loop is deferred (see Trade-offs).
- **Failure and timeout are first-class, never silent.** Every failure bucket
  maps to a clear user-facing message: `infra_error` → "code execution is
  temporarily unavailable"; `retry_exhausted` → a fallback that shows the code
  it tried and **refuses to invent a number**. When the agent can't compute
  reliably, it says so; it never reaches for a plausible-looking figure. A
  timeout or OOM is reported as a stopped run, not a wrong answer.
- **A provenance badge** tells the user whether the answer was *computed in the
  sandbox* (with execution count) or *answered directly* — and flags
  `suspected_unrouted_computation` when a prose answer contains figures it
  should have computed. The user can catch a mis-route the routing policy
  missed.

### 8. Observability — per-turn records

Every turn emits one structured JSONL record (`observability/`): a `turn_id`
grouping all executions and retries, the route taken (prose turns logged too),
the generated code, the exact data snapshot + its hash, the **full system prompt
+ its hash**, the model, token usage, and per-execution outcome. A record is
**self-contained**, so any execution replays deterministically as
`Runner.run(ExecutionRequest(code, snapshot, …))` and any LLM turn can be
reconstructed exactly as the model saw it — which is why no separate replay
script was built.

Storing the full prompt per record (not hash-only) trades disk for guaranteed
replayability: an engineer debugging a wrong answer never has to wonder *what
prompt the model actually saw*, even if the prompt code has since changed. The
hash is the cheap correlation key (*"find every turn that ran prompt X"*). At
scale the per-record copy can become a content-addressed prompt registry keyed
by hash; at beta volumes "full text per record" wins on operational simplicity.

### 9. The data layer is a swappable seam over mock data

The POC stops at a `Repository` returning a typed `DataProfile` (schema, enum
values, date ranges, null counts) plus the row data. Everything upstream — the
orchestrator, the prompt builder, the sandbox — consumes only that contract; the
backing is in-memory mock data (`data/seed.py`).

Building Django models, migrations, auth, a tenant model, and onboarding would
have consumed days without proving anything about the execution-layer question
the brief asks. So the data layer is built as the **interface production would
consume**, with the backing left as mock. Swapping the backing — to Django ORM
with `django-tenants` and pgvector — touches nothing upstream, because the seam
was designed for it: a backend job, not an architecture job (Iteration Path, step 2).
It also keeps the *wrong* assumption out of the build. Caseload scale, the
tenant boundary, the real field set — those are first-week stakeholder questions
(see [Stakeholder Discovery](./stakeholder-discovery.md)), not things to guess at
in code.

---

## Consequences

### What this buys us

- A working, demonstrable code-execution loop with guardrails that visibly fire
  (`scripts/demo_guardrails.py` — deterministic, no LLM flakiness).
- Privacy that's enforced by architecture, not policy: raw case data never
  reaches the LLM or the network.
- Failure handling that stays honest: bounded retries, a clear message per
  failure class, no invented numbers.
- A clean upgrade path (Lambda / gVisor) that needs no rearchitecting.

### Trade-offs and what we are deliberately deferring

| Deferred | Risk it carries | Mitigation / production plan                                                                                   |
|---|---|----------------------------------------------------------------------------------------------------------------|
| **gVisor / Firecracker** not in the POC | Shared-kernel container escape via a kernel 0-day | `runtime=` / `Runner` swap to managed Lambda/Firecracker or self-hosted gVisor (see *Sandbox isolation*).      |
| **Real data layer** (Django models, auth, multi-tenancy, pgvector) | POC ≠ product; backing is mock | Swap the mock backing for Django ORM + `django-tenants` + pgvector (Iteration Path, step 2).                   |
| **Warm container pool / queue / scaling** | No concurrency beyond one host; the reaper's `jupus.sandbox=1` label would force-remove a peer replica's live containers if scaled out naively | AWS Lambda instead of a self-hosted pool — a `Runner` swap (Iteration Path, step 1).                           |
| **Result-verification layer** | A snippet can compute a *silently wrong* value | An assertion/verification layer (Iteration Path, step 4).                                                      |
| **JavaScript execution** | Python-only | A separate scoped effort (Iteration Path, step 5); would need to be justified. Current plans don't justify it. |
| **Logs as a PII surface** | Records hold code + a raw data snapshot | Redaction, a retention policy, and an audit store (today: `0600`, git-ignored, local-only).                    |
| **True token streaming** | The final answer is word-streamed by the UI, not streamed from the model | Token streaming through the tool loop — a UI refinement (see *Streaming & UX*).                                |

### Residual risks we accept for the beta

- **Unrouted computation** — the model could still answer a quantitative
  question in prose. Reduced by the routing policy + few-shot; made *visible* by
  the post-hoc guard and the UI badge (see *Streaming & UX*).
- **Orchestrator privilege** — the orchestrator host holds Docker-daemon access
  (root-equivalent) and the `GEMINI_API_KEY`: the two things an escape onto the
  host could reach. Invariant held in code: **no LLM-controlled
  value reaches a `docker-py` `create()` parameter** (image, mounts, limits,
  env, command) — only the snippet and data-file contents. The Lambda path
  removes this structurally: `docker.sock` becomes a scoped
  `lambda:InvokeFunction` IAM role (Iteration Path, step 1).

---

## Alternatives Considered

**On the framing — "did we even need a sandbox?"** These reframe the brief, not
just the implementation; naming them explicitly is the point.

- **SQL-only / text-to-SQL.** All four example queries are expressible in SQL —
  Postgres `NUMERIC` solves decimals, date arithmetic is native, grouping and
  filtering are SQL's whole job. **Rejected** because (a) we need a flexible
  primitive for future inputs (uploaded files, pasted images, multi-step
  transforms that don't fit one SELECT); (b) the LLM needs full schema awareness
  regardless — SQL doesn't save us from "model has to know the data shape"; (c)
  Python can *decide* to write SQL when that's right — the reverse isn't
  possible; (d) Python snippets are inspectable and debuggable in ways SQL isn't
  (intermediate prints, step-by-step reasoning in the log). "Try SQL first, fall
  back to Python" is a defensible production refinement, but the primitive must
  be Python.
- **Pre-defined function library (no code-gen).** Expose
  `sum_claims`/`provision`/`group_outcomes` as tools the model picks from. Zero
  arbitrary execution. **Rejected** because a legal product takes arbitrary
  input — *"cases where the client's name starts with B and the fine is more
  than 3× the median"* — and a fixed function set cannot keep pace. The brief
  names *ad-hoc file parsing* as a pain point, confirming the flexibility need.

**On the sandbox isolation tech** (the runtime choices behind *Sandbox isolation*):

- **gVisor (`runsc`).** A userspace kernel that intercepts the container's
  syscalls; the host kernel sees only the gVisor "sentry," so a kernel exploit
  hits gVisor first, not the host. Cost: gVisor's ~20% syscall overhead, some workloads
  break. *Deferred to production:* hardened Docker is genuinely strong for our
  threat model (LLM-generated code is not adversarial-human); gVisor matters when
  the threat model changes (multi-tenant SaaS, public-facing, user-uploaded
  code). Adoption is a `runtime=runsc` swap, so the cost of waiting is low.
- **Firecracker microVMs.** Hardware-enforced isolation (KVM), used by AWS
  Lambda. Stronger in *adversarial-human-tenant* scenarios. For **our** threat
  model (one tenant per session, LLM-generated code, no user uploads) the
  marginal isolation over hardened Docker + gVisor is small and the operational
  cost (microVM lifecycle, custom kernel + rootfs, jailer) is large — which is
  exactly why we reach it *via managed Lambda* rather than self-hosting it (see *Sandbox isolation*).
- **WASM / Pyodide.** Isolation-by-construction (no syscalls, FS, or network).
  Structurally elegant, and graded highly in research. **Deferred for this POC**
  because the author hasn't built production WASM sandboxes and a takehome isn't
  the moment to learn — honesty matters. In production, if WASM is the strongest
  fit for our threat model, the right move is to invest the learning curve.
- **RestrictedPython / in-process sandboxing.** Rejected outright. Python cannot
  be sandboxed in-process; audit hooks and restricted builtins are bypassable.
  This is *why* the result contract uses a process boundary.

**On the integration shape:**

- **Managed sandboxing (E2B, Modal, Riza, Judge0).** Production-credible;
  per-execution Firecracker out of the box. *Rejected for the POC* on two
  grounds: (a) the exercise is to demonstrate our *own* sandbox design; (b)
  **privacy is a real constraint** — sending raw caseload rows to a third-party
  sandbox adds an external data-egress surface that contradicts the privacy
  mandate. The production equivalent we'd consider isn't a managed sandbox; it's
  **self-hosting an open-source model on EU territory** (GDPR / attorney-client
  privilege) with our own hardened Docker / gVisor sandbox on the same infra.
- **A separate `retrieve_data` tool** — rejected for the POC: the air-gap injects
  the scoped dataset anyway; at ~20 rows a retrieval tool adds surface without
  value. (It returns in production — Iteration Path, step 2.)
- **No sandbox / bare `subprocess`** — rejected: not a security boundary.
- **OpenAI Responses API as the provider, or a multi-provider runtime behind a
  flag** — both credible, both rejected for the POC. The `LLMClient` abstraction
  (see *Agent integration*) makes adding OpenAI a one-file change; *running* two
  providers in parallel
  is speculative generality a beta doesn't need (YAGNI).

---

## Iteration Path (beta → production)

The migration mechanics behind the decisions above. Each step names only what
*changes* and the rules that **cannot bend**.

1. **Move the sandbox to AWS Lambda (managed Firecracker).** Deploy the same
   image to ECR as a Lambda container image; swap `Runner` to invoke it via
   `boto3.client("lambda").invoke()`. One step delivers the microVM isolation
   upgrade (see *Sandbox isolation*), horizontal scale (AWS's default of 1,000 concurrent executions
   per account, raisable on request — removing the per-host budget, the
   warm-pool design, and the reaper), and removal of the orchestrator-privilege
   risk (`docker.sock` → scoped IAM role).

   Three rules this transition cannot bend on:
   - **The warm-reuse invariant.** Lambda reuses execution environments to
     amortise cold starts, so "no leak between runs" must be enforced *inside the
     handler* — clear `/tmp`, never hold case data in module globals, freshen
     input per call. Per-execution containers become per-execution *handlers*;
     the discipline transfers, the boundary moves.
   - **Payload size.** Synchronous invoke caps at 6 MB. Past that, route input
     via S3 with an IAM role scoped to one key prefix — which pairs naturally
     with step 2, where the snippet receives a *scoped retrieval result*, not the
     full caseload.
   - **New `infra_error` sub-reasons.** Lambda throttling
     (`TooManyRequestsException`), init failures, and provisioned-concurrency
     exhaustion are new failure modes the 5-bucket classifier absorbs. The
     buckets stay; the sub-reasons grow.

   *(Self-hosted gVisor on EC2/k8s is the alternative when Lambda is unsuitable —
   see *Sandbox isolation*. The `Runner` abstraction targets either; the air-gap
   and result contract
   are shared.)*

2. **Integrate into Django + pgvector/RAG retrieval.** The POC injects the full
   caseload every turn (`repository.all_cases()`), which falls over around 10K
   cases. Production swaps in `repository.relevant_cases(...)` and adds a
   `retrieve_cases` tool so the agent fetches a scoped subset before computing.

   Two rules this transition cannot bend on:
   - **Tenant isolation is enforced by infrastructure, not application code, and
     never by the LLM.** Per-tenant Postgres schemas (`django-tenants`) are the
     starting point; separate databases when the regime justifies the ops cost.
     `tenant_id` is set once at auth and threaded through trusted server code —
     not the agent, the dispatcher, or the LLM.
   - **The LLM controls retrieval *intent*; the server controls retrieval
     *scope*.** The agent passes a `semantic_query` and whitelisted
     `metadata_filters`; the server injects `tenant_id` and clamps `limit`. A
     prompt injection saying "retrieve tenant 43's data" can't escalate because
     `tenant_id` was never a parameter the LLM could pass.

   *(Note: pgvector HNSW + shared-table RLS has known recall issues when one
   tenant is a small fraction of the index; per-tenant schemas or partial indices
   work around it. Which fits depends on tenant count and strictness.)*

3. **Promote observability:** tracing, peak-memory sampling, alerting on
   `suspected_unrouted_computation` and containment events, an execution
   dashboard; redaction + retention for the PII in records. (On Lambda, much of
   this is CloudWatch / X-Ray; the per-turn JSONL record stays authoritative for
   replay.)
4. **Add a result-verification layer** to catch silently-wrong values (see *the result contract*).
5. **Revisit multi-language execution (JavaScript)** as a separate, scoped
   effort.
6. **Cleaner Computation↔Conversation boundary.** The orchestrator currently
   reads sandbox-domain constants (`BUCKET_OK`, `BUCKET_INFRA_ERROR`) to drive
   retry policy. Replace with semantic predicates on `ExecutionResult`
   (`is_retryable_code_error()`, `is_infrastructure_failure()`) so the
   conversation layer consumes a behavioural contract, not the sandbox's internal
   vocabulary. Small refactor; removes a coupling that compounds as more backends
   land.

*Deprioritised:* running the snippet as a grandchild subprocess so an uncaught
crash sets an unforgeable exit code. It sits below the sandbox boundary (so it
would apply to Docker and Lambda alike), but it sharpens crash *classification*,
not result trust — and Decision 4 already establishes that a forged result
grants no escalation, only a wrong value. Not worth a roadmap slot.

---

## Stakeholder Discovery

The build proceeded on assumptions appropriate for a beta. The questions a real
version of this work resolves in the first week — organised by stakeholder (PM,
AI Lead, Head of Engineering, chat-product devs) — are in
**[stakeholder-discovery.md](./stakeholder-discovery.md)**.
