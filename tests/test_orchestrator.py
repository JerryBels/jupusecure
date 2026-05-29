"""Tests for the agent orchestrator: the prose-vs-code loop.

The LLM client and the sandbox runner are both faked, so these tests drive
the full routing/retry/fallback logic with **no network** and **no Docker**.
The fake client implements the same ``agent.llm.LLMClient`` protocol as the
production client; swapping LLM providers therefore needs no test changes.
"""

from __future__ import annotations

import json

import pytest

from agent.llm import (LLMAuthError, LLMQuotaError, LLMResponse,
                       LLMUnavailableError, ToolCall, ToolCallResult)
from agent.orchestrator import (_INFRA_ANSWER, CodeEvent, FinalEvent,
                                Orchestrator, OrchestratorError, StatusEvent)
from agent.prompts import RETRY_EXHAUSTED_ANSWER
from data.repository import CaseRepository
from data.seed import seed
from observability.logger import TurnLogger
from sandbox.runner import (BUCKET_INFRA_ERROR, BUCKET_OK,
                            BUCKET_RETRYABLE_CODE_ERROR, ExecutionResult)


# --- fakes -------------------------------------------------------------------
class FakeLLMClient:
    """Scripted ``LLMClient``. Mirrors the production client's surface; the
    orchestrator cannot tell the difference."""

    def __init__(self, scripted: list[LLMResponse],
                 system_prompt: str = "fake-system-prompt",
                 system_prompt_hash: str = "fake-prompt-hash") -> None:
        self._responses = list(scripted)
        self.user_calls: list[str] = []
        self.tool_result_calls: list[list[ToolCallResult]] = []
        self.system_prompt = system_prompt
        self.system_prompt_hash = system_prompt_hash

    def send_user(self, text: str) -> LLMResponse:
        self.user_calls.append(text)
        return self._responses.pop(0)

    def send_tool_results(self, results: list[ToolCallResult]) -> LLMResponse:
        self.tool_result_calls.append(results)
        return self._responses.pop(0)


class FakeRunner:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self._results = list(results)
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return self._results.pop(0)


def _ok(result: object = 42) -> ExecutionResult:
    return ExecutionResult(bucket=BUCKET_OK, sub_reason="ok", status="ok",
                           result=result, result_type="scalar")


def _fail(sub: str = "runtime_error") -> ExecutionResult:
    return ExecutionResult(bucket=BUCKET_RETRYABLE_CODE_ERROR, sub_reason=sub,
                           status=sub, error="KeyError: 'x'")


def _infra() -> ExecutionResult:
    return ExecutionResult(bucket=BUCKET_INFRA_ERROR,
                           sub_reason="docker_unavailable", error="docker down")


def _call(call_id: str, code: str, purpose: str = "compute") -> ToolCall:
    return ToolCall(call_id=call_id, name="execute_python",
                    arguments_json=json.dumps({"code": code, "purpose": purpose}))


def _prose(text: str) -> LLMResponse:
    return LLMResponse(text=text)


def _calls(*tool_calls: ToolCall) -> LLMResponse:
    return LLMResponse(tool_calls=list(tool_calls))


def _drive(tmp_path, scripted, runner_results):
    db = tmp_path / "cases.db"
    seed(db)
    client = FakeLLMClient(scripted)
    runner = FakeRunner(runner_results)
    logger = TurnLogger(tmp_path / "log.jsonl")
    orchestrator = Orchestrator(CaseRepository(db), runner, logger,
                                client=client, model="fake-model")
    record = None
    for event in orchestrator.run_turn("a question", session_id="t"):
        if isinstance(event, FinalEvent):
            record = event.record
    return record, client, runner, logger


# --- prose route -------------------------------------------------------------
def test_prose_route_answers_without_executing(tmp_path):
    record, _, runner, _ = _drive(
        tmp_path, [_prose("A provision is a financial reserve.")], [])
    assert record.route == "prose"
    assert record.attempts == []
    assert runner.requests == []


def test_prose_answer_with_figures_is_flagged(tmp_path):
    record, _, _, _ = _drive(
        tmp_path, [_prose("There are 5 open cases right now.")], [])
    assert record.suspected_unrouted_computation is True


# --- code route --------------------------------------------------------------
def test_code_route_executes_and_answers(tmp_path):
    record, _, runner, logger = _drive(
        tmp_path,
        [_calls(_call("c1", "result = 42")), _prose("The total is 42.")],
        [_ok(42)],
    )
    assert record.route == "code"
    assert len(record.attempts) == 1
    assert record.final_answer == "The total is 42."
    assert record.final_outcome == BUCKET_OK
    assert len(logger.read_all()) == 1


def test_failed_execution_is_retried_then_succeeds(tmp_path):
    record, _, runner, _ = _drive(
        tmp_path,
        [_calls(_call("c1", "result = bad")),
         _calls(_call("c2", "result = good")),
         _prose("Fixed: the answer is 42.")],
        [_fail(), _ok(42)],
    )
    assert len(record.attempts) == 2
    assert record.attempts[0].bucket == BUCKET_RETRYABLE_CODE_ERROR
    assert record.attempts[1].bucket == BUCKET_OK
    assert record.final_answer == "Fixed: the answer is 42."


def test_retry_budget_exhaustion_gives_honest_fallback(tmp_path):
    record, _, _, _ = _drive(
        tmp_path,
        [_calls(_call("c1", "result = a")),
         _calls(_call("c2", "result = b")),
         _calls(_call("c3", "result = c"))],
        [_fail(), _fail()],
    )
    assert record.final_outcome == "retry_exhausted"
    assert record.final_answer == RETRY_EXHAUSTED_ANSWER
    assert len(record.attempts) == 2  # bounded


def test_identical_failure_twice_stops_early(tmp_path):
    """Same code, same failure -> stop before spending another model call."""
    record, client, _, _ = _drive(
        tmp_path,
        [_calls(_call("c1", "result = same")),
         _calls(_call("c2", "result = same"))],
        [_fail(), _fail()],
    )
    assert record.final_outcome == "retry_exhausted"
    # One initial send_user + one send_tool_results = 2 client interactions.
    assert len(client.user_calls) == 1
    assert len(client.tool_result_calls) == 1


def test_infra_error_is_not_retried(tmp_path):
    record, client, _, _ = _drive(
        tmp_path, [_calls(_call("c1", "result = 1"))], [_infra()])
    assert record.final_outcome == "infra_error"
    assert record.final_answer == _INFRA_ANSWER
    assert client.tool_result_calls == []  # never sent the result back


def test_malformed_tool_arguments_do_not_spawn_an_execution(tmp_path):
    record, _, runner, _ = _drive(
        tmp_path,
        [_calls(ToolCall("c1", "execute_python", "not-json")),
         _prose("Recovered answer.")],
        [],
    )
    assert runner.requests == []
    assert record.attempts == []
    assert record.final_answer == "Recovered answer."


# --- streaming / robustness --------------------------------------------------
def test_running_status_is_streamed_before_the_sandbox_runs(tmp_path):
    """The 'running' status event must reach the consumer before runner.run
    is invoked -- otherwise the UI cannot show status while code executes."""
    db = tmp_path / "cases.db"
    seed(db)
    client = FakeLLMClient([_calls(_call("c1", "result = 1")), _prose("Done.")])
    runner = FakeRunner([_ok(1)])
    orchestrator = Orchestrator(CaseRepository(db), runner,
                                TurnLogger(tmp_path / "l.jsonl"),
                                client=client, model="fake")
    saw_running = False
    for event in orchestrator.run_turn("q", session_id="t"):
        if isinstance(event, CodeEvent):
            assert runner.requests == []
        if isinstance(event, StatusEvent) and event.stage == "running":
            assert runner.requests == []
            saw_running = True
    assert saw_running and len(runner.requests) == 1


def test_llm_api_error_is_handled_gracefully(tmp_path):
    """An exception from the model client must not crash the turn -- it
    becomes a graceful answer and a logged record carrying the error."""

    class _RaisingClient:
        system_prompt = "fake-system-prompt"
        system_prompt_hash = "fake"

        def send_user(self, text):
            raise RuntimeError("api exploded")

        def send_tool_results(self, results):
            raise RuntimeError("api exploded")

    db = tmp_path / "cases.db"
    seed(db)
    logger = TurnLogger(tmp_path / "log.jsonl")
    orchestrator = Orchestrator(CaseRepository(db), FakeRunner([]), logger,
                                client=_RaisingClient(), model="fake")
    record = None
    for event in orchestrator.run_turn("q", session_id="t"):
        if isinstance(event, FinalEvent):
            record = event.record
    assert record.final_outcome == "error"
    assert "api exploded" in record.error
    assert "went wrong" in record.final_answer.lower()
    assert len(logger.read_all()) == 1


def _run_with_raising_client(tmp_path, exception):
    """Drive one turn against a client that raises ``exception`` on send_user."""

    class _RaisingClient:
        system_prompt = "fake-system-prompt"
        system_prompt_hash = "fake"

        def send_user(self, text):
            raise exception

        def send_tool_results(self, results):
            raise exception

    db = tmp_path / "cases.db"
    seed(db)
    logger = TurnLogger(tmp_path / "log.jsonl")
    orchestrator = Orchestrator(CaseRepository(db), FakeRunner([]), logger,
                                client=_RaisingClient(), model="fake")
    record = None
    for event in orchestrator.run_turn("q", session_id="t"):
        if isinstance(event, FinalEvent):
            record = event.record
    return record


def test_auth_error_gives_actionable_user_message(tmp_path):
    record = _run_with_raising_client(
        tmp_path, LLMAuthError("400 API key not valid"))
    assert record.final_outcome == "error"
    assert "GEMINI_API_KEY" in record.final_answer
    assert "API key not valid" in record.error


def test_quota_error_gives_actionable_user_message(tmp_path):
    record = _run_with_raising_client(tmp_path, LLMQuotaError("429 quota"))
    assert record.final_outcome == "error"
    msg = record.final_answer.lower()
    assert "rate limit" in msg or "quota" in msg


def test_unavailable_error_gives_actionable_user_message(tmp_path):
    record = _run_with_raising_client(
        tmp_path, LLMUnavailableError("503 service unavailable"))
    assert record.final_outcome == "error"
    assert "couldn't reach" in record.final_answer.lower()


def test_empty_model_answer_gets_a_fallback(tmp_path):
    record, _, _, _ = _drive(tmp_path, [_prose("")], [])
    assert record.final_answer.strip()
    assert "rephras" in record.final_answer.lower()


def test_record_uses_clients_system_prompt_hash(tmp_path):
    """The recorded hash must reflect the prompt the model actually saw --
    i.e. the cached client's hash, not a recomputation. Otherwise the audit
    log silently drifts when the data profile changes mid-session."""
    db = tmp_path / "cases.db"
    seed(db)
    client = FakeLLMClient([_prose("done")],
                           system_prompt_hash="locked-session-hash")
    logger = TurnLogger(tmp_path / "log.jsonl")
    orchestrator = Orchestrator(CaseRepository(db), FakeRunner([]), logger,
                                client=client, model="fake")
    record = None
    for event in orchestrator.run_turn("q", "t"):
        if isinstance(event, FinalEvent):
            record = event.record
    assert record.system_prompt_hash == "locked-session-hash"


def test_record_stores_full_system_prompt(tmp_path):
    """The full prompt text must land in the record, not just its hash --
    that's what makes a record fully replayable even after the prompt code
    is edited (ADR §7)."""
    db = tmp_path / "cases.db"
    seed(db)
    client = FakeLLMClient([_prose("done")],
                           system_prompt="LOCKED PROMPT TEXT v1")
    logger = TurnLogger(tmp_path / "log.jsonl")
    orchestrator = Orchestrator(CaseRepository(db), FakeRunner([]), logger,
                                client=client, model="fake")
    record = None
    for event in orchestrator.run_turn("q", "t"):
        if isinstance(event, FinalEvent):
            record = event.record
    assert record.system_prompt == "LOCKED PROMPT TEXT v1"


def test_same_client_across_turns_accumulates_history(tmp_path):
    """An injected client persists across two run_turn calls; both user
    messages reach it -- this is what gives the chat its session memory."""
    db = tmp_path / "cases.db"
    seed(db)
    client = FakeLLMClient([_prose("first answer"), _prose("second answer")])
    logger = TurnLogger(tmp_path / "log.jsonl")

    for query in ("first q", "second q"):
        orchestrator = Orchestrator(CaseRepository(db), FakeRunner([]), logger,
                                    client=client, model="fake")
        for _ in orchestrator.run_turn(query, "t"):
            pass

    # Both queries reached the same client instance.
    assert client.user_calls == ["first q", "second q"]


def test_orchestrator_raises_when_no_api_key_is_set(tmp_path, monkeypatch):
    """The production client construction path (no `client=` injected) must
    fail loudly when GEMINI_API_KEY is missing -- the error a reviewer sees
    if they run the chat without a .env."""
    import config
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    db = tmp_path / "cases.db"
    seed(db)
    with pytest.raises(OrchestratorError, match="GEMINI_API_KEY"):
        Orchestrator(CaseRepository(db), FakeRunner([]),
                     TurnLogger(tmp_path / "log.jsonl"))
