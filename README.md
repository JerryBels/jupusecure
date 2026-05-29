# Jupus — Secure Code-Execution Layer for the AI Chat

A proof-of-concept that lets an AI chat agent safely run **LLM-generated
Python** to answer questions a language model gets wrong on its own — date
arithmetic, decimal maths, aggregations over a legal caseload.

The agent decides, per query, whether to answer in prose or to run code. When
it runs code, the snippet executes in a fresh, hardened, **air-gapped** Docker
container with strict resource limits; the result is captured and woven back
into a natural-language answer.

- **Design rationale:** [`docs/ADR.md`](docs/ADR.md)
- **Requirements & plan:** [`docs/brainstorms/`](docs/brainstorms/) · [`docs/plans/`](docs/plans/)

```
user query → agent (Gemini, via LLMClient protocol) ─┬─ answer in prose
                                            └─ execute_python(code)
                                                 → hardened ephemeral container
                                                   (no network · read-only FS ·
                                                    non-root · cgroup limits)
                                                 → result → final answer
```

## Prerequisites

- **Docker Desktop**, running. The sandbox executes code in containers.
- **Python 3.12+**.
- A **Gemini API key** (free tier at https://aistudio.google.com/apikey) —
  needed only for the chat agent (`app.py`, `scripts/cli.py`). The sandbox
  and the guardrail demo run **without** a key. The LLM provider is isolated
  behind an `LLMClient` protocol — swapping to another provider is a
  one-file change (see ADR-001 §5).

## Setup

```bash
# 1. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure (only needed for the chat agent)
cp .env.example .env        # then add your GEMINI_API_KEY

# 3. Build the sandbox container image
docker build -t jupus-sandbox sandbox/image
```

The mock legal-case database is **seeded automatically** on the first run of
`streamlit run app.py` or `python scripts/cli.py …` -- both entrypoints
print one ``[setup] Seeded mock caseload at …`` line and proceed. To seed
manually (e.g. to reset the dataset), run ``python -m data.seed``.

## Running

### The guardrail demo — no API key needed

Feeds fixed, hostile snippets straight to the sandbox and shows each guardrail
firing. This is the deterministic security demonstration.

```bash
python scripts/demo_guardrails.py
```

Expected: a blocked network call, a blocked filesystem write, a thread bomb and
a memory bomb stopped by cgroup limits, an infinite loop killed by the timeout,
and an envelope-forgery attempt contained.

### The chat app

```bash
streamlit run app.py
```

Try the four example queries:

- *"What's the total value of claims filed in Q1 2026 vs Q1 2025?"*
- *"Calculate the provision for case #4821."*  (30% of the €45,000 fine = €13,500)
- *"Show a breakdown of case outcomes by category for the last 6 months."*
- *"How many days between filing and hearing for each open case?"*

### The CLI harness

```bash
python scripts/cli.py "What is the total claim value filed in Q1 2026?"
```

### Inspect the turn log

Every chat turn writes one structured record to `logs/executions.jsonl`
(generated code, input snapshot hash, model, token usage, per-execution
outcome, error if any — see ADR §7). To pretty-print the most recent record:

```bash
python scripts/show_log.py           # the last turn
python scripts/show_log.py -n 5      # the last 5
```

When something goes wrong in the chat, this is where the real exception lives —
the user-facing message stays brief; the full provider error is in `error`.

### Tests

```bash
python -m pytest
```

The pure-logic tests (data layer, the result contract, outcome classification,
the agent retry/routing loop) run with no Docker and no network. The container
integration tests in `tests/test_runner.py` run only when Docker is available
and the image is built, and skip otherwise.

## How it works

| Layer | What it does | Key files |
|---|---|---|
| Chat UI | Thin Streamlit chat; live status, generated code, badge | `app.py` |
| Agent | Routing policy, tool calling, bounded retry, honest fallback. The `LLMClient` is cached per Streamlit session so chat memory persists across turns. | `agent/` |
| Sandbox | Hardened container lifecycle, outcome classification | `sandbox/runner.py` |
| In-container | Trusted parent + child process; the result-envelope contract | `sandbox/image/` |
| Data | Mock caseload; the LLM-facing data profile (no raw rows) | `data/` |
| Observability | Per-turn, replay-capable JSONL records | `observability/` |

**The security model is the air-gap.** The sandbox has no network and no
credentials. Raw case data is injected only into the container; the LLM sees
only a schema/aggregate *profile*, never raw client rows. The untrusted snippet
runs in a child process and cannot forge the result envelope. See the ADR for
the full reasoning and the precise integrity claim.

## Project layout

```
app.py                     Streamlit chat UI
config.py                  Central configuration & resource limits
agent/                     Orchestrator, prompts, the execute_python tool
sandbox/
  runner.py                Host-side container lifecycle + classification
  image/                   Dockerfile, trusted entrypoint, child runner
data/                      Mock caseload (seed) + repository / data profile
observability/             Per-turn records + JSONL logger
scripts/                   demo_guardrails.py, cli.py
tests/                     Pure-logic tests + Docker integration tests
docs/                      ADR, video runbook, brainstorm + plan
```

## Known limitations (deliberate, beta scope)

The container runtime is `runc`; gVisor/Firecracker are the documented
production hardening step. There is no warm pool, no Django/pgvector
integration, no result-verification layer, and Python-only execution. Each is
discussed — with its risk and production plan — in
[`docs/ADR.md`](docs/ADR.md).
