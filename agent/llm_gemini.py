"""Gemini implementation of ``LLMClient``.

The ONLY file in the project that imports ``google.genai``. Conversation
state (the ``contents`` list) lives entirely here; the orchestrator stays
provider-agnostic.

Adding a different provider (Anthropic, OpenAI, etc.) means writing one
sibling module that implements ``LLMClient`` -- no other file changes.
"""

from __future__ import annotations

import hashlib
import json

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from agent.llm import (LLMAuthError, LLMError, LLMQuotaError, LLMResponse,
                       LLMUnavailableError, TokenUsage, ToolCall,
                       ToolCallResult, ToolDefinition)


def _translate_api_error(exc: genai_errors.APIError) -> LLMError:
    """Map a google-genai APIError to our provider-neutral taxonomy.

    The orchestrator uses the resulting type to choose an actionable user
    message. The original error message is preserved for the audit log.
    """
    code = getattr(exc, "code", None) or 0
    details = getattr(exc, "details", None) or {}
    nested = ((details.get("error") or {}).get("details") or []) \
        if isinstance(details, dict) else []
    key_invalid = any(
        isinstance(d, dict) and d.get("reason") == "API_KEY_INVALID"
        for d in nested
    )

    if code in (401, 403) or key_invalid:
        return LLMAuthError(str(exc))
    if code == 429:
        return LLMQuotaError(str(exc))
    if 500 <= code < 600:
        return LLMUnavailableError(str(exc))
    return LLMError(str(exc))


class GeminiClient:
    """Stateful per-turn conversation against the Gemini API."""

    def __init__(self, api_key: str, model: str, system_prompt: str,
                 tools: list[ToolDefinition]) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters=t.parameters,
                ) for t in tools
            ])],
        )
        self._contents: list[types.Content] = []
        self._call_index = 0  # synthetic ids -- Gemini's function_call.id is optional
        self.system_prompt = system_prompt
        self.system_prompt_hash = hashlib.sha256(
            system_prompt.encode("utf-8")).hexdigest()

    # -- LLMClient protocol ---------------------------------------------------
    def send_user(self, text: str) -> LLMResponse:
        self._contents.append(types.Content(
            role="user", parts=[types.Part.from_text(text=text)]))
        try:
            return self._step()
        except Exception:
            # Roll back: the call did not reach the model, so don't leave an
            # orphan user message in history. The cached session client would
            # otherwise send [user, user, ...] on the next call, confusing
            # the model's accounting of what it has actually answered.
            self._contents.pop()
            raise

    def send_tool_results(self, results: list[ToolCallResult]) -> LLMResponse:
        parts = [types.Part.from_function_response(
            name=r.call.name,
            response=self._payload_to_dict(r.payload_json),
        ) for r in results]
        self._contents.append(types.Content(role="user", parts=parts))
        try:
            return self._step()
        except Exception:
            self._contents.pop()
            raise

    # -- internal -------------------------------------------------------------
    def _step(self) -> LLMResponse:
        try:
            response = self._client.models.generate_content(
                model=self._model, contents=self._contents, config=self._config,
            )
        except genai_errors.APIError as exc:
            raise _translate_api_error(exc) from exc
        # Append the model's content (which carries any function_call parts)
        # so the next turn has the full history.
        candidate = (response.candidates or [None])[0]
        if candidate is not None and candidate.content is not None:
            self._contents.append(candidate.content)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else []) or []:
                text = getattr(part, "text", None)
                if text:
                    text_parts.append(text)
                function_call = getattr(part, "function_call", None)
                if function_call is not None:
                    self._call_index += 1
                    tool_calls.append(ToolCall(
                        call_id=getattr(function_call, "id", None)
                                or f"c{self._call_index}",
                        name=function_call.name,
                        arguments_json=json.dumps(dict(function_call.args or {})),
                    ))

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            ),
        )

    @staticmethod
    def _payload_to_dict(payload_json: str) -> dict:
        """Gemini wants function-response payloads as dicts, not raw strings."""
        try:
            decoded = json.loads(payload_json)
        except json.JSONDecodeError:
            return {"result": payload_json}
        return decoded if isinstance(decoded, dict) else {"result": decoded}
