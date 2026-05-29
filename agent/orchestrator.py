"""The agent orchestrator: the prose-vs-code loop.

Drives one user turn end to end:
  1. Build the system prompt (routing policy + data profile).
  2. Ask the model. It either answers in prose or calls ``execute_python``.
  3. Run any tool call in the sandbox; feed the outcome back.
  4. Retry on a retryable failure, bounded at ``MAX_TOOL_ITERATIONS`` rounds.
  5. Produce a final answer -- or an honest fallback if the budget is spent.
  6. Emit one structured ``TurnRecord``.

This module is **provider-agnostic**: it only talks to the model through the
``LLMClient`` protocol (``agent/llm.py``). Adding a different provider is a
one-file change (write a sibling of ``agent/llm_gemini.py`` implementing
``LLMClient``) plus one wire-up swap in ``_default_client_factory`` below.

``run_turn`` is a generator: it yields progress events live as the sandbox
runs and a final event carrying the ``TurnRecord``. The LLM client is
injected, so tests drive the loop with a ``FakeLLMClient`` and no network.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Iterator
from dataclasses import dataclass, field

import config
from agent.llm import (LLMAuthError, LLMClient, LLMError, LLMQuotaError,
                       LLMUnavailableError, TokenUsage, ToolCall,
                       ToolCallResult, ToolDefinition)
from agent.prompts import (RETRY_EXHAUSTED_ANSWER, build_system_prompt,
                           looks_like_unrouted_computation, retry_instruction)
from agent.tools import EXECUTE_PYTHON_TOOL
from observability.records import (ROUTE_CODE, ROUTE_PROSE, ExecutionAttempt,
                                   TurnRecord, new_execution_id, new_turn_id,
                                   sha256)
from sandbox.runner import (BUCKET_INFRA_ERROR, BUCKET_OK, ExecutionRequest,
                            Runner)

_INFRA_ANSWER = (
    "Code execution is temporarily unavailable, so I can't compute this right "
    "now. Please try again shortly."
)
_TOOL_STDOUT_CAP = 4000  # keep tool output context-cheap

# Actionable user messages for each LLM error class. The full provider error
# is preserved on ``record.error`` for the audit log; the user sees the short
# version. Order matters: most specific first (isinstance match).
_LLM_ERROR_MESSAGES: list[tuple[type, str]] = [
    (LLMAuthError, "I couldn't reach the AI provider — the API key was "
                   "rejected. Check `GEMINI_API_KEY` in your `.env`."),
    (LLMQuotaError, "The AI provider's rate limit or quota was hit. Please "
                    "wait a minute and try again."),
    (LLMUnavailableError, "I couldn't reach the AI provider — it may be down "
                          "or your network is unreachable. Please try again "
                          "shortly."),
    (LLMError, "The AI provider returned an error. Please try again."),
]
_UNEXPECTED_ERROR_MESSAGE = ("Something unexpected went wrong. The error is "
                             "logged for review.")


def _user_message_for(exc: Exception) -> str:
    for error_class, message in _LLM_ERROR_MESSAGES:
        if isinstance(exc, error_class):
            return message
    return _UNEXPECTED_ERROR_MESSAGE


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class OrchestratorError(RuntimeError):
    """Raised for unrecoverable orchestrator setup problems (e.g. no API key)."""


@dataclass(frozen=True)
class ToolArguments:
    """Validated arguments parsed from a single ``execute_python`` tool call."""

    code: str
    purpose: str  # short LLM-supplied description -- "" if the model omitted it


# -- progress events yielded by ``Orchestrator.run_turn`` ---------------------
# Typed events replace the earlier ``{"type": "...", ...}`` dicts so consumers
# (Streamlit UI, CLI, tests) get autocomplete and refactor-safe matching.

@dataclass(frozen=True)
class StatusEvent:
    """A stage transition the UI should reflect (drafting/running/retrying)."""

    stage: str
    purpose: str | None = None


@dataclass(frozen=True)
class CodeEvent:
    """A snippet the model drafted, surfaced live before the sandbox runs it."""

    code: str
    purpose: str


@dataclass(frozen=True)
class AnswerEvent:
    """The final natural-language answer for this turn."""

    text: str


@dataclass(frozen=True)
class FinalEvent:
    """The structured TurnRecord -- emitted last, after the answer."""

    record: "TurnRecord"


# A consumer that wants to switch on event type does ``isinstance(event, ...)``;
# the union is the closed set of what ``run_turn`` can yield.
TurnEvent = StatusEvent | CodeEvent | AnswerEvent | FinalEvent


@dataclass
class _RoundOutcome:
    """The terminal decision produced by one tool-call round.

    ``final_answer is not None`` means the round itself concluded the turn
    (infra error, exhausted/oscillating retry); otherwise the loop continues
    by sending ``tool_results`` back to the model.
    """

    failure_signature: tuple | None
    tool_results: list[ToolCallResult] = field(default_factory=list)
    final_answer: str | None = None
    final_outcome: str | None = None


def _default_client_factory(model: str, system_prompt: str,
                            tools: list[ToolDefinition]) -> LLMClient:
    """Build the production LLM client. **The only provider-specific line
    in this module.** Swap the import + class to change provider."""
    if not config.GEMINI_API_KEY:
        raise OrchestratorError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add a "
            "key (the sandbox and demo_guardrails.py run without one)."
        )
    from agent.llm_gemini import GeminiClient
    return GeminiClient(api_key=config.GEMINI_API_KEY, model=model,
                        system_prompt=system_prompt, tools=tools)


def build_session_client(repository) -> LLMClient:
    """Build an LLMClient meant to live for an entire chat session.

    Callers (the Streamlit app) cache this once per session and pass it to
    each per-turn Orchestrator, so the model has **conversation memory** across
    turns — without it, every turn starts the model's memory at zero and a
    user saying "try again" has no context to refer to.

    The system prompt is fixed at client-build time, which is acceptable for
    a beta with a static data profile; production would rebuild the client
    when the profile changes (or pass the prompt per-call).
    """
    return _default_client_factory(
        config.GEMINI_MODEL,
        build_system_prompt(repository.data_profile()),
        [EXECUTE_PYTHON_TOOL],
    )


class Orchestrator:
    """Runs the agent loop for one chat session."""

    def __init__(self, repository, runner: Runner, logger,
                 client: LLMClient | None = None,
                 model: str | None = None) -> None:
        self.repository = repository
        self.runner = runner
        self.logger = logger
        self.model = model or config.GEMINI_MODEL
        self._client = client  # may be None; built per-turn if so
        # Fail loudly at construction if neither a client nor a key is present.
        if self._client is None and not config.GEMINI_API_KEY:
            raise OrchestratorError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and "
                "add a key."
            )

    # -- public ---------------------------------------------------------------
    def run_turn(self, query: str, session_id: str) -> Iterator[TurnEvent]:
        """Run one user turn, yielding progress events then a final event."""
        cases_json = self.repository.cases_json()

        # Build a fallback client only when none was injected (the CLI path);
        # the Streamlit app always passes a session-cached client.
        if self._client is None:
            client = _default_client_factory(
                self.model,
                build_system_prompt(self.repository.data_profile()),
                [EXECUTE_PYTHON_TOOL],
            )
        else:
            client = self._client

        record = TurnRecord(
            turn_id=new_turn_id(), session_id=session_id, timestamp=_now_iso(),
            user_query=query, model=self.model,
            # Read from the actual client -- the prompt the model truly saw,
            # not a freshly-rebuilt one. (Fixes audit-log drift when a cached
            # client outlives the per-turn prompt-rebuild.)
            system_prompt=client.system_prompt,
            system_prompt_hash=client.system_prompt_hash,
            data_snapshot_hash=sha256(cases_json), data_snapshot=cases_json,
        )
        token_usage = TokenUsage()
        rounds = 0
        prev_failure_signature: tuple | None = None
        final_answer = ""

        try:
            yield StatusEvent(stage="drafting")
            response = client.send_user(query)
            token_usage = token_usage + response.usage

            while True:
                if not response.tool_calls:
                    final_answer = response.text.strip()
                    record.route = ROUTE_CODE if record.attempts else ROUTE_PROSE
                    record.final_outcome = (
                        record.attempts[-1].bucket if record.attempts
                        else ROUTE_PROSE
                    )
                    break

                if rounds >= config.MAX_TOOL_ITERATIONS:
                    final_answer = RETRY_EXHAUSTED_ANSWER
                    record.route = ROUTE_CODE
                    record.final_outcome = "retry_exhausted"
                    break

                rounds += 1
                outcome = yield from self._execute_tool_calls(
                    response.tool_calls, record, cases_json, session_id,
                    prev_failure_signature)
                prev_failure_signature = outcome.failure_signature

                if outcome.final_answer is not None:
                    final_answer = outcome.final_answer
                    record.route = ROUTE_CODE
                    record.final_outcome = outcome.final_outcome
                    break

                next_stage = ("retrying" if any(attempt.bucket != BUCKET_OK
                                                 for attempt in record.attempts)
                              else "drafting")
                yield StatusEvent(stage=next_stage)
                response = client.send_tool_results(outcome.tool_results)
                token_usage = token_usage + response.usage
        except Exception as exc:  # noqa: BLE001 - a turn must never crash the UI
            final_answer = _user_message_for(exc)
            record.route = ROUTE_CODE if record.attempts else ROUTE_PROSE
            record.final_outcome = "error"
            record.error = f"{type(exc).__name__}: {exc}"

        if not final_answer.strip():
            final_answer = ("I couldn't produce an answer for that. Please "
                            "try rephrasing your question.")

        if record.route == ROUTE_PROSE and looks_like_unrouted_computation(final_answer):
            record.suspected_unrouted_computation = True

        record.final_answer = final_answer
        record.token_usage = token_usage
        self.logger.log(record)
        yield AnswerEvent(text=final_answer)
        yield FinalEvent(record=record)

    # -- tool-call round ------------------------------------------------------
    def _execute_tool_calls(self, tool_calls: list[ToolCall], record: TurnRecord,
                            cases_json: str, session_id: str,
                            prev_failure_signature: tuple | None):
        """Execute every tool call in one round.

        Generator: yields ``code``/``status`` events **live** (before
        ``runner.run`` is called), and ``return``s a ``_RoundOutcome`` whose
        ``tool_results`` the orchestrator sends back to the model.
        """
        failure_signature = prev_failure_signature
        final_answer: str | None = None
        final_outcome: str | None = None
        tool_results: list[ToolCallResult] = []

        for call in tool_calls:
            arguments = self._parse_args(call)
            if arguments is None:
                tool_results.append(ToolCallResult(call, json.dumps({
                    "status": "error",
                    "detail": "The tool call had invalid or empty arguments. "
                              "Resend with a non-empty `code` string and a "
                              "`purpose`.",
                })))
                continue

            yield CodeEvent(code=arguments.code, purpose=arguments.purpose)
            yield StatusEvent(stage="running", purpose=arguments.purpose)

            execution_id = new_execution_id()
            result = self.runner.run(ExecutionRequest(
                code=arguments.code, cases_json=cases_json, session_id=session_id,
                execution_id=execution_id, purpose=arguments.purpose,
            ))
            record.attempts.append(self._build_attempt(
                len(record.attempts) + 1, execution_id, arguments, result))
            tool_results.append(ToolCallResult(
                call, json.dumps(self._tool_payload(result))))

            if result.bucket == BUCKET_INFRA_ERROR:
                final_answer, final_outcome = _INFRA_ANSWER, "infra_error"
                continue

            if result.bucket != BUCKET_OK:
                signature = (arguments.code, result.sub_reason)
                if signature == failure_signature:
                    final_answer = RETRY_EXHAUSTED_ANSWER
                    final_outcome = "retry_exhausted"
                failure_signature = signature
            else:
                failure_signature = None

        return _RoundOutcome(failure_signature=failure_signature,
                             tool_results=tool_results,
                             final_answer=final_answer,
                             final_outcome=final_outcome)

    # -- payload / args helpers (provider-neutral) ---------------------------
    @staticmethod
    def _parse_args(call: ToolCall) -> ToolArguments | None:
        try:
            parsed = json.loads(call.arguments_json)
        except (json.JSONDecodeError, TypeError):
            return None
        code = parsed.get("code") if isinstance(parsed, dict) else None
        if not isinstance(code, str) or not code.strip():
            return None
        purpose = parsed.get("purpose")
        return ToolArguments(
            code=code,
            purpose=purpose if isinstance(purpose, str) else "",
        )

    @staticmethod
    def _tool_payload(result) -> dict:
        """The dict the LLM sees as a function-call result. JSON-shaped on
        purpose -- this is wire format, not a domain object."""
        if result.bucket == BUCKET_OK:
            return {
                "status": "ok", "result": result.result,
                "result_type": result.result_type,
                "stdout": (result.stdout or "")[:_TOOL_STDOUT_CAP],
            }
        return {
            "status": "failed", "bucket": result.bucket,
            "sub_reason": result.sub_reason,
            "detail": retry_instruction(result.bucket, result.sub_reason,
                                        result.error, result.traceback),
        }

    @staticmethod
    def _build_attempt(attempt_index: int, execution_id: str,
                       arguments: ToolArguments, result) -> ExecutionAttempt:
        preview = None
        if result.result is not None:
            preview = json.dumps(result.result)[:200]
        return ExecutionAttempt(
            attempt=attempt_index, execution_id=execution_id,
            purpose=arguments.purpose, code=arguments.code,
            bucket=result.bucket, sub_reason=result.sub_reason,
            status=result.status, duration_ms=result.duration_ms,
            exit_code=result.exit_code, oom_killed=result.oom_killed,
            timed_out=result.timed_out, error=result.error, result_preview=preview,
            result_source=result.result_source,
        )
