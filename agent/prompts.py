"""System and retry prompts for the agent.

The system prompt does three jobs:
  1. States a HARD routing policy (prose vs. code) -- the prose-vs-code
     decision is governed by an explicit, enumerated rule plus few-shot
     examples, not left to the model's instinct.
  2. Embeds the data profile rendered by the Caseload context -- the prompt
     builder does NOT reach into the data shape; ``DataProfile`` owns its
     own ``render_for_llm()`` so adding a new case field touches only the
     Caseload code (see data/profile.py).
  3. Pins the edge policies (inclusive date ranges, Decimal money, null dates,
     empty groups) and the result contract.
"""

from __future__ import annotations

import re

from data.profile import DataProfile

# A prose answer that contains any of these is suspicious -- it likely needed
# computation and should have been routed to the tool. Used by the post-hoc guard.
_NUMERIC_SIGNAL = re.compile(r"\d|€|\$|\bpercent\b", re.IGNORECASE)


def looks_like_unrouted_computation(answer: str) -> bool:
    """True if a prose answer looks like it silently computed something."""
    return bool(_NUMERIC_SIGNAL.search(answer or ""))


def build_system_prompt(data_profile: DataProfile) -> str:
    """Assemble the system prompt around the data profile's own rendering."""
    return f"""\
You are the analytical assistant inside Jupus, a legal-tech product. Lawyers \
ask questions about their caseload in natural language. Your job is to answer \
accurately.

You are unreliable at arithmetic, date logic, and aggregation when you do it \
in your head. For anything quantitative you MUST use the `execute_python` \
tool, which runs real Python in a secure sandbox.

# ROUTING POLICY (decide this first, every turn)

Call `execute_python` if the answer depends on ANY of:
  - counting or aggregating case records
  - date arithmetic (durations, "last quarter", "three months ago", ranges)
  - money or decimal computation (totals, provisions, percentages)
  - filtering cases by field values
  - grouping or breaking down cases

Answer directly in prose ONLY for definitional or explanatory questions that \
touch no case data (e.g. "what does 'provision' mean?"). When in doubt, call \
the tool. Never state a number, amount, count, or date from memory.

Examples:
  - "How many open cases are there?" -> call execute_python
  - "Total claim value filed in Q1?" -> call execute_python
  - "What is a provision?" -> answer in prose
  - "Which cases involve client X?" -> call execute_python

# THE DATA

{data_profile.render_for_llm()}

# COMPUTATION RULES (pin these in your code)

  - Money: claim_amount and fine_amount are decimal STRINGS. Always wrap them \
in decimal.Decimal. Never use float for money. Round money results with \
.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).
  - Provision rule: a provision is 30% of the fine -> fine * Decimal("0.30").
  - Date ranges are INCLUSIVE of both endpoints. Parse dates with \
datetime.date.fromisoformat.
  - hearing_date and closing_date can be null -- skip or handle null before \
date arithmetic.
  - When grouping, report categories with a zero count explicitly.

# RESULT CONTRACT

In every snippet, assign the final answer to a variable named `result`. \
Optionally set `result_type` to "scalar", "table", or "text". You may print() \
intermediate values for transparency. After a tool call, write a short, clear \
natural-language answer for the lawyer; before you do, sanity-check `result` \
against the question (sign, magnitude, units).
"""


def retry_instruction(bucket: str, sub_reason: str, error: str | None,
                      traceback_text: str | None) -> str:
    """Feedback appended after a failed execution so the model self-corrects."""
    detail = (error or "").strip()
    if traceback_text:
        detail += "\n\nTraceback:\n" + _strip_internal_frames(traceback_text)

    if bucket == "resource_exceeded":
        guidance = (
            f"The sandbox stopped the code: {sub_reason}. Reasoning will not "
            "fix this -- you must change the approach: process less data, "
            "avoid unbounded loops, and do not allocate large structures."
        )
    else:
        guidance = (
            "The code failed. Emit a corrected `execute_python` call. Fix the "
            "specific error below. Do NOT answer in prose -- the user needs a "
            "computed answer."
        )
    return f"{guidance}\n\n{detail}"


RETRY_EXHAUSTED_ANSWER = (
    "I wasn't able to compute this reliably after several attempts, so I "
    "won't give you a number I'm not sure of. You can see the code I tried "
    "below. Please rephrase the question or narrow it, and I'll try again."
)


def _strip_internal_frames(traceback_text: str) -> str:
    """Remove sandbox-internal frames so the model sees only its own code."""
    kept = [
        line for line in traceback_text.splitlines()
        if "child_runner.py" not in line
        and "entrypoint.py" not in line
        and "/sandbox-app/" not in line
    ]
    return "\n".join(kept)
