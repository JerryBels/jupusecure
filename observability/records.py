"""Structured, replay-capable records for one agent turn.

A *turn* is one user query and everything the agent did to answer it: the
routing decision, every sandbox execution and retry, and the final answer.
Recording at the turn level (not the execution level) is what lets you answer
"why did the agent give this answer" -- including the case where it answered
in prose when it should have computed (``suspected_unrouted_computation``).

A record is self-contained: it carries the generated code, the exact data
snapshot, and the full system prompt the model saw, so any execution can be
re-run deterministically as ``Runner.run(ExecutionRequest(code, snapshot, ...))``
and any LLM turn can be reconstructed exactly as it ran, even after the prompt
code has since been edited. That is why no separate replay script is needed.
See ADR-001 §7 for the rationale (full prompt vs. content-addressed registry).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from agent.llm import TokenUsage

ROUTE_PROSE = "prose"
ROUTE_CODE = "code"

# ``final_outcome`` values that mean "this turn did NOT produce a normal
# computed answer" -- the user gets a fallback message and consumers
# (UI badge, CLI warn prefix) should flag the turn distinctly.
NON_OK_OUTCOMES = frozenset({"error", "infra_error", "retry_exhausted"})


def new_turn_id() -> str:
    """Identifier grouping all executions and retries within one user turn."""
    return uuid.uuid4().hex[:12]


def new_execution_id() -> str:
    """Identifier for a single sandbox execution."""
    return uuid.uuid4().hex[:12]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ExecutionAttempt:
    """One sandbox execution within a turn (a turn may have several on retry)."""

    attempt: int
    execution_id: str
    purpose: str
    code: str
    bucket: str
    sub_reason: str
    status: str | None
    duration_ms: int
    exit_code: int | None
    oom_killed: bool
    timed_out: bool
    error: str | None
    result_preview: str | None
    result_source: str | None = None   # which variable the snippet's answer came from


@dataclass
class TurnRecord:
    """Everything the agent did to answer one user query."""

    turn_id: str
    session_id: str
    timestamp: str
    user_query: str
    route: str = ROUTE_PROSE
    model: str = ""
    system_prompt: str = ""           # full prompt text -- replay primitive (see ADR §7)
    system_prompt_hash: str = ""      # sha256(system_prompt) -- correlation primitive
    data_snapshot_hash: str = ""
    data_snapshot: str = ""           # raw rows injected this turn (PII surface)
    attempts: list[ExecutionAttempt] = field(default_factory=list)
    final_answer: str = ""
    final_outcome: str = ROUTE_PROSE  # last bucket, or "prose"
    suspected_unrouted_computation: bool = False
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    error: str | None = None          # set when the turn failed unexpectedly
