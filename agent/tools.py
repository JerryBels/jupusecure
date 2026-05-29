"""The single tool the agent can call: ``execute_python``.

Defined as a provider-neutral ``ToolDefinition``. Each ``LLMClient``
implementation adapts this to its provider's tool envelope.

The description is the agent-facing context the model re-reads on every call.
It states the sandbox's capabilities and the result contract, so the model
writes code that fits without a separate discovery round-trip.
"""

from __future__ import annotations

from agent.llm import ToolDefinition

EXECUTE_PYTHON_TOOL = ToolDefinition(
    name="execute_python",
    description=(
        "Run a short, self-contained Python snippet in a secure sandbox to "
        "compute an exact answer over the user's legal caseload. Use this for "
        "anything involving counting, summing, date arithmetic, money/decimal "
        "maths, filtering, or grouping.\n\n"
        "The sandbox: Python 3.12 standard library only (no third-party "
        "packages); NO network; NO filesystem access; one run is fully "
        "isolated from every other.\n\n"
        "A variable named `cases` is ALREADY defined -- a list of dicts, one "
        "per case, matching the schema in the system prompt. Do not try to "
        "load data; just use `cases`.\n\n"
        "RESULT CONTRACT: assign your final answer to a variable named "
        "`result`. Money amounts are decimal strings -- wrap them in "
        "decimal.Decimal, never float. The sandbox returns `result` plus "
        "anything you print()."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python snippet to execute. Must assign `result`.",
            },
            "purpose": {
                "type": "string",
                "description": (
                    "One short sentence stating what this snippet computes, "
                    "e.g. 'Sum claim_amount for cases filed in Q1 2026.'"
                ),
            },
        },
        "required": ["code", "purpose"],
    },
)
