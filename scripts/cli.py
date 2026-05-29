"""Command-line harness for the agent loop.

Runs one query end to end without the Streamlit UI -- useful for testing the
four example queries and for the video walkthrough.

Run:  python scripts/cli.py "What is the total claim value filed in Q1 2026?"
Requires: GEMINI_API_KEY in .env, and the sandbox image built.

Single-shot: this CLI builds a fresh ``LLMClient`` per invocation, so it does
NOT have the multi-turn memory the Streamlit app gets via
``build_session_client`` cached in ``st.session_state``. For "try again"-style
follow-ups, use the chat UI; for programmatic looping, build a session client
once and reuse it across ``Orchestrator(...)`` constructions.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from agent.orchestrator import (AnswerEvent, CodeEvent, FinalEvent,  # noqa: E402
                                Orchestrator, OrchestratorError, StatusEvent)
from data.repository import CaseRepository  # noqa: E402
from data.seed import ensure_seeded  # noqa: E402
from observability.logger import TurnLogger  # noqa: E402
from observability.records import NON_OK_OUTCOMES  # noqa: E402
from sandbox.runner import Runner  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print('usage: python scripts/cli.py "your question"')
        return 2

    query = argv[1]
    ensure_seeded(config.CASES_DB_PATH)

    with Runner() as runner:
        if not runner.ping():
            print("Docker is not available -- start Docker Desktop and retry.")
            return 2

        try:
            orchestrator = Orchestrator(
                CaseRepository(config.CASES_DB_PATH), runner, TurnLogger())
        except OrchestratorError as exc:
            print(exc)
            return 2

        print(f"\nQ: {query}\n")
        record = None
        for event in orchestrator.run_turn(query, session_id="cli"):
            if isinstance(event, StatusEvent):
                print(f"  ... {event.stage}")
            elif isinstance(event, CodeEvent):
                print(f"\n  --- generated code ({event.purpose}) ---")
                for line in event.code.splitlines():
                    print(f"  | {line}")
                print()
            elif isinstance(event, AnswerEvent):
                print(f"\nA: {event.text}\n")
            elif isinstance(event, FinalEvent):
                record = event.record

        if record is not None:
            if record.final_outcome in NON_OK_OUTCOMES:
                print(f"[warn] no computed answer ({record.final_outcome}) -- "
                      "the answer above is a fallback message")
            if record.suspected_unrouted_computation:
                print("[warn] suspected_unrouted_computation: answer contains "
                      "figures but the sandbox did not run")
            print(f"[route={record.route} outcome={record.final_outcome} "
                  f"executions={len(record.attempts)} tokens={record.token_usage}]")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
