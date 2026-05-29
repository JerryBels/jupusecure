---
date: 2026-05-21
topic: code-execution-sandbox
---

# Jupus Takehome — Secure Code-Execution Layer for the AI Chat

## Problem Frame

Jupus is a legal-tech startup whose AI chat lets lawyers query their caseload in
natural language. The LLM handles prose well but is unreliable at **deterministic
computation** — date arithmetic, decimal maths, aggregations, structured-data
manipulation. Three pain points: inconsistent date reasoning, no computational
ability, and privacy/cost concerns from offloading raw records to the model.

This is a **Staff Engineer takehome assignment**. The deliverable is a
proof-of-concept of a secure server-side code-execution pathway the chat agent can
invoke when language generation won't produce a reliable answer, plus an ADR and a
video. The assignment evaluates end-to-end ownership: architecture under ambiguity,
security judgement, scope discipline, and stakeholder communication. Scope
discipline is itself graded — a tight, well-reasoned solution beats an overbuilt one.

## Requirements

### Code-execution loop
- **R1.** A Streamlit chat surface takes a user query and routes it to an LLM agent.
  The agent uses tool/function calling: it either answers in prose directly, or
  invokes an `execute_python` tool when the query needs deterministic computation.
- **R2.** When the tool is invoked, the LLM drafts a short, self-contained Python
  routine that operates on a provided dataset (not arbitrary data access).
- **R3.** The orchestrator performs data retrieval against a mocked legal-case
  dataset and passes only a **minimal, scoped dataset** into the sandbox. The
  sandbox is air-gapped — no DB credentials, no network, no host filesystem.
- **R4.** Code runs in an **ephemeral hardened Docker container**: network disabled,
  read-only root filesystem, tmpfs scratch space, non-root user, all capabilities
  dropped, seccomp profile, cgroup memory/CPU/PID limits, and a wall-clock timeout.
  The container is destroyed after each run.
- **R5.** Results (numeric value, table, or artifact) are captured from the sandbox
  via a structured output contract, returned to the orchestrator, and woven back
  into the chat answer by the LLM.
- **R11.** The POC handles the brief's four example query types end-to-end:
  date-filtered sum, the 30% provision rule, a grouped outcome breakdown table, and
  date arithmetic across multiple records.

### Security, privacy, failure modes
- **R6.** The POC visibly demonstrates guardrails firing on camera: a blocked
  network call, a blocked filesystem write, and resource exhaustion stopped
  (fork bomb → PID cap, runaway memory → cgroup limit, infinite loop → timeout).
  Each produces a clean, captured failure — not a crash.
- **R7.** Invalid syntax, runtime errors, timeouts, and non-zero exits are caught.
  The user sees a friendly message; full detail goes to logs. At most one bounded
  retry on invalid generated code.
- **R8.** Hermetic execution — no state, data, or artifacts leak between executions
  or sessions.

### Streaming & UX
- **R9.** The chat surfaces execution status (drafting code → running in sandbox →
  computing) and streams the final answer. Timeouts and failures degrade gracefully
  in the UI.

### Observability
- **R10.** Every execution emits a structured log record: execution id, session id,
  triggering query, **full generated code**, input dataset snapshot/hash, exit code,
  duration, peak memory, truncated stdout/stderr, and any guardrail-trip reason.
  The design goal: any execution is **fully replayable from logs** — this is what
  makes the "silently wrong calculation" debugging scenario answerable.

### Deliverables
- **R12.** A README with setup instructions.
- **R13.** An ADR (Context / Decision / Consequences / Alternatives Considered) that
  names deferred items and presents the two-tier sandbox path.
- **R14.** A Loom video: a leadership pitch for the feature, plus the three
  technical deep-dive questions, referencing actual POC code and real past
  experience.

## Success Criteria

- The full loop runs end-to-end on camera: query → agent decides → code drafted →
  sandboxed run → result → answer in chat.
- At least three distinct guardrails are shown blocking dangerous operations live.
- A failure case (broken/problematic generated code) is run live and handled
  gracefully.
- The ADR clearly states the chosen approach, the trade-offs, and what is
  deliberately deferred, with alternatives ruled out for stated reasons.
- The video communicates architectural reasoning and maps each deep-dive answer to
  both POC code and authentic past experience.

## Scope Boundaries

Deliberately out of scope for the POC — each named in the ADR with its risk and
mitigation:

- **No real Django integration** — Streamlit + a standalone runner service; the ADR
  explains how it slots into the existing Django app.
- **No real pgvector/RAG** — a mocked, seeded legal-case dataset stands in for
  document retrieval.
- **gVisor / Firecracker not in the POC** — documented as the production hardening
  path (see Key Decisions).
- **No warm container pool, queue, or horizontal scaling** — single-node,
  cold-start per run; acceptable for a private beta.
- **Python only** — JavaScript execution deliberately deferred. (This is also the
  hook for the Q2 prioritisation scenario about adding JS mid-sprint.)
- **No auth or multi-tenancy** beyond demonstrated hermetic isolation.
- **No execution-history dashboard** — logs are structured records, not a UI.
- **No tracing/alerting stack** (OpenTelemetry, on-call alerts) — named as iteration.

## Key Decisions

- **Effort: standard ~5-7 days.** Matches the brief's expected level.
- **Two-tier sandbox.** Hardened Docker container in the POC; gVisor (`runsc`)
  runtime and Firecracker microVMs presented in the ADR as the concrete production
  hardening path. Rationale: Docker is built on Linux-security primitives the author
  genuinely knows, every guardrail is demonstrable on camera, and it works on the
  author's macOS Docker Desktop. The honest acknowledgement of the shared-kernel
  limitation — with a named next step — is itself the Staff-level signal.
- **Privacy by architecture.** The orchestrator does retrieval; the sandbox is
  air-gapped and receives only a minimal scoped dataset. Untrusted LLM-generated
  code never holds DB credentials or network access, so data exfiltration is
  structurally prevented, not merely policed.
- **Agent decides via LLM tool/function calling.** The model either answers in prose
  or calls `execute_python` — the code-vs-prose decision falls out of the tool-use
  contract rather than a separate classifier.
- **OpenAI API** for the LLM integration — the author has direct API experience and
  a published Lambda layer for it; the integration is kept swappable.
- **Streamlit** for the chat surface, per the brief's explicit guidance to borrow
  scaffolding and invest effort in the sandbox.
- **Mocked SQLite legal-case dataset**, seeded to satisfy all four example queries
  (fields: case id, client, category, filing date, hearing date, status, claim
  amount, fine, outcome).

## Dependencies / Assumptions

- Docker Desktop available locally (macOS).
- An OpenAI API key is available.
- AWS is assumed as the target cloud for the production-path discussion in the ADR.
- "Beta-quality prototype" means correctness and security are real; scale,
  polish, and ops tooling are intentionally thin.

## Video Narrative Anchors

Authentic past-experience material to map into the deep-dives (refine during prep):

- **Q1 (securing untrusted input/code):** Linux server hardening; handling untrusted
  external input in telecom carrier/number-portability integrations and SIM
  provisioning at the MVNO; CRM payment integration.
- **Q2 (shipping under pressure):** solo-architecting and shipping Buddzy to both
  app stores; greenfield national MVNO from zero; building a full CRM from zero as
  CTO.
- **Q3 (production incident, AI/infra):** an AI-pipeline or provider failure across
  the multi-provider setup at Buddzy (Gemini/OpenAI/Replicate + AWS Lambda), or a
  GenAI-integration incident at BrainPOP.

## Outstanding Questions

### Deferred to Planning
- [Affects R4][Technical] Exact seccomp profile and capability set to ship with the
  container.
- [Affects R5][Technical] Result-passing contract across the sandbox boundary —
  structured stdout (JSON) vs. a mounted result file.
- [Affects R2][Technical] Which Python libraries are preinstalled in the sandbox
  image (stdlib only vs. pandas/numpy) — bounds what generated code can do.
- [Affects R7][Needs research] Whether and how tightly to bound a retry loop on
  invalid generated code.
- [Affects R9][Technical] Streamlit streaming mechanics for live status plus the
  final answer.

## Next Steps

→ `/ce:plan` for structured implementation planning. No questions block planning;
the deferred items above are implementation choices best resolved there.
