"""Tests for the GeminiClient that exercise host-side behaviour without
calling the real API.

We use a fake API key (``genai.Client(api_key=...)`` is lazy and doesn't
validate it until the first request) and monkey-patch ``_step`` to simulate
provider failures.
"""

from __future__ import annotations

import pytest

from agent.llm import ToolCall, ToolCallResult
from agent.llm_gemini import GeminiClient


def _client() -> GeminiClient:
    return GeminiClient(api_key="fake-test-key", model="fake",
                        system_prompt="sys", tools=[])


def test_system_prompt_hash_is_exposed_and_stable():
    client = _client()
    assert client.system_prompt_hash and len(client.system_prompt_hash) == 64
    # Same prompt -> same hash; different prompt -> different hash.
    other = GeminiClient(api_key="fake-test-key", model="fake",
                         system_prompt="different", tools=[])
    assert other.system_prompt_hash != client.system_prompt_hash


def test_system_prompt_full_text_is_exposed():
    """The client must surface the full prompt text -- the orchestrator
    stamps it onto every TurnRecord so records remain replayable even
    after the prompt code is edited (ADR §7)."""
    client = GeminiClient(api_key="fake-test-key", model="fake",
                          system_prompt="LOCKED PROMPT", tools=[])
    assert client.system_prompt == "LOCKED PROMPT"


def test_send_user_rolls_back_on_failure():
    """A failed _step must not leave an orphan user message in history --
    the cached session client would then send [user, user, ...] next call."""
    client = _client()
    assert client._contents == []          # baseline

    def boom():
        raise RuntimeError("provider boom")

    client._step = boom
    with pytest.raises(RuntimeError):
        client.send_user("hello")
    assert client._contents == []          # rolled back


def test_send_tool_results_rolls_back_on_failure():
    """Same rollback semantics for the tool-result code path."""
    client = _client()

    def boom():
        raise RuntimeError("provider boom")

    client._step = boom
    call = ToolCall(call_id="c1", name="execute_python", arguments_json="{}")
    with pytest.raises(RuntimeError):
        client.send_tool_results([ToolCallResult(call, '{"status": "ok"}')])
    assert client._contents == []
