"""Streamlit chat UI for the Jupus caseload assistant.

Deliberately thin -- the assignment's effort belongs in the sandbox, not the
chat layer. This wires the repository, runner, and orchestrator together and
renders: live status, the generated code, the streamed answer, and a badge
that tells the user whether the answer was computed or answered directly.

Run:  streamlit run app.py
"""

from __future__ import annotations

import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

import config  # noqa: E402
from agent.orchestrator import (AnswerEvent, CodeEvent, FinalEvent,  # noqa: E402
                                Orchestrator, OrchestratorError, StatusEvent,
                                build_session_client)
from data.repository import CaseRepository  # noqa: E402
from data.seed import ensure_seeded  # noqa: E402
from observability.logger import TurnLogger  # noqa: E402
from observability.records import NON_OK_OUTCOMES  # noqa: E402
from sandbox.runner import Runner  # noqa: E402

st.set_page_config(page_title="Jupus Caseload Assistant", page_icon="⚖️")
st.title("⚖️ Jupus Caseload Assistant")
st.caption(
    "Ask about your caseload. Quantitative questions run real Python in a "
    "secure, isolated sandbox."
)

_STATUS_LABELS = {
    "drafting": "Drafting an approach…",
    "running": "Running in the secure sandbox…",
    "retrying": "First attempt failed — retrying…",
}


@st.cache_resource
def _components():
    """Build shared components once; reap any orphaned sandbox containers.

    First run: seeds the mock SQLite caseload if it doesn't exist yet, so
    reviewers reproducing the demo can just ``streamlit run app.py`` without
    a separate setup step. After the file exists this is a no-op.
    """
    ensure_seeded(config.CASES_DB_PATH)
    runner = Runner()
    if runner.ping():
        runner.reap_orphans()
    return CaseRepository(config.CASES_DB_PATH), runner


def _stream_words(text: str):
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.012)


def _render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("code"):
                with st.expander("View generated code"):
                    st.code(message["code"], language="python")
            if message.get("badge"):
                st.caption(message["badge"])


def _badge(record) -> str:
    if record.final_outcome in NON_OK_OUTCOMES:
        return "⚠️ The answer above isn't a normal result — see the message"
    if record.route == "code":
        return f"\U0001f512 Computed in the sandbox · {len(record.attempts)} execution(s)"
    badge = "\U0001f4ac Answered directly (no computation)"
    if record.suspected_unrouted_computation:
        badge += " · ⚠️ answer contains figures but no computation ran"
    return badge


repository, runner = _components()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:8]
if "llm_client" not in st.session_state:
    # Built lazily on the first prompt; cached for the whole session so the
    # model has conversation memory across turns.
    st.session_state.llm_client = None
if "busy" not in st.session_state:
    st.session_state.busy = False

_render_history()

# Streamlit serialises script reruns per session, so concurrent submits are
# already blocked at the framework level; the `disabled` here makes the
# assumption explicit in the UI and guards against any rare interleave
# during the `with st.status(...)` / `st.write_stream` block below.
prompt = st.chat_input("Ask about your caseload…",
                       disabled=st.session_state.busy)
if prompt:
    st.session_state.busy = True
    try:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                if st.session_state.llm_client is None:
                    st.session_state.llm_client = build_session_client(repository)
                orchestrator = Orchestrator(
                    repository, runner, TurnLogger(),
                    client=st.session_state.llm_client,
                )
            except OrchestratorError as exc:
                st.error(str(exc))
                st.stop()

            code_shown: str | None = None
            record = None
            answer = ""

            with st.status("Working…", expanded=False) as status:
                for event in orchestrator.run_turn(prompt, st.session_state.session_id):
                    if isinstance(event, StatusEvent):
                        label = _STATUS_LABELS.get(event.stage, event.stage)
                        status.update(label=label)
                        st.write(f"• {label}")
                    elif isinstance(event, CodeEvent):
                        code_shown = event.code
                        st.write(f"• Drafted code — *{event.purpose}*")
                    elif isinstance(event, AnswerEvent):
                        answer = event.text
                    elif isinstance(event, FinalEvent):
                        record = event.record
                status.update(label="Done", state="complete")

            st.write_stream(_stream_words(answer))
            if code_shown:
                with st.expander("View generated code"):
                    st.code(code_shown, language="python")
            badge = _badge(record) if record else None
            if badge:
                st.caption(badge)

        st.session_state.messages.append({
            "role": "assistant", "content": answer,
            "code": code_shown, "badge": badge,
        })
    finally:
        # Always clear the busy flag -- including on st.stop() (which raises
        # StopException) or any unexpected error -- so the chat input doesn't
        # stay disabled across the next rerun.
        st.session_state.busy = False
