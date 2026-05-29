"""Provider-neutral LLM abstraction.

The orchestrator depends only on the types and ``LLMClient`` protocol defined
here. Adding a second provider is a one-file change: implement ``LLMClient``
in a new module and switch one wire-up line in the orchestrator. The
orchestrator stays free of provider-specific shapes (input lists, contents,
parts, function_call_outputs, etc.).

Tests **never** call a real LLM API. The orchestrator suite injects a
``FakeLLMClient`` that implements this protocol; live API calls are exercised
only by the user via ``app.py`` / ``scripts/cli.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ToolDefinition:
    """A tool the model may call, described in neutral JSON Schema.

    Each provider client adapts this to its own function/tool envelope.
    """

    name: str
    description: str
    parameters: dict  # JSON Schema: {"type":"object","properties":{...},"required":[...]}


@dataclass(frozen=True)
class ToolCall:
    """One model-issued tool invocation.

    ``call_id`` is opaque -- it's whatever the provider gave us, or a
    synthetic id when the provider doesn't supply one. It is round-tripped
    back via ``ToolCallResult`` so the provider can match the result.
    """

    call_id: str
    name: str
    arguments_json: str  # may be malformed; the orchestrator validates


@dataclass(frozen=True)
class ToolCallResult:
    """The orchestrator's reply to a single tool call."""

    call: ToolCall
    payload_json: str  # JSON-encoded outcome the model will see


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for one model call -- provider-neutral."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class LLMResponse:
    """A single response from the model.

    ``text`` is any natural-language text the model emitted (final answer or
    reasoning preamble). ``tool_calls`` is non-empty when the model wants the
    orchestrator to execute tools and reply.
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMError(Exception):
    """Base for provider failures that ``LLMClient`` implementations translate.

    The orchestrator catches these to produce actionable user messages,
    instead of collapsing every provider error into a single generic line.
    """


class LLMAuthError(LLMError):
    """The provider rejected our authentication (invalid or missing key)."""


class LLMQuotaError(LLMError):
    """The provider rate-limited us or our quota was exhausted."""


class LLMUnavailableError(LLMError):
    """The provider is transiently unreachable (5xx, network, timeout)."""


class LLMClient(Protocol):
    """A stateful conversation with the model.

    The client owns the conversation history. The orchestrator only sends
    user messages and tool results; it never touches provider-specific
    history shapes. One client instance handles one user turn (which may
    include several tool-call rounds) or, when cached in the Streamlit
    session, an entire chat conversation.
    """

    system_prompt: str
    """The full system prompt text this client was constructed with.

    Stored on every TurnRecord so the record is **fully replayable** -- an
    engineer debugging a wrong answer can see exactly what the model read,
    even if the prompt code has since been edited. See ADR §7.
    """

    system_prompt_hash: str
    """sha256 of ``system_prompt``.

    Kept alongside the full text as the cheap identity primitive for
    cross-record correlation ("find every turn that ran prompt X"). The
    full text is the replay primitive; the hash is the lookup primitive.
    """

    def send_user(self, text: str) -> LLMResponse:
        """Append a user message and get the model's next response."""

    def send_tool_results(self, results: list[ToolCallResult]) -> LLMResponse:
        """Append a batch of tool results and get the model's next response."""
