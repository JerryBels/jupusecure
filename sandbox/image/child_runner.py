"""Runs the untrusted snippet inside the sandbox CHILD process.

This module is trusted code, but it deliberately shares a process with the
untrusted snippet it ``exec()``s. It therefore makes no security guarantee on
its own. The security boundary is twofold and lives entirely outside this file:

  1. The container (hardening + resource limits), enforced by the host runner.
  2. The parent process (``entrypoint.py``), which runs separately, owns the
     container's real stdout, and emits the one canonical result envelope.

This module's only job: run the snippet, resolve whatever it computed, and
write a structured result document to a file the parent reads. A snippet can
corrupt that file -- but the value it computes is the snippet's to produce
anyway, and a snippet can never reach the parent process or the host's
classification. See ADR-001 for the precise integrity claim.
"""

from __future__ import annotations

import datetime
import io
import json
import sys
import traceback
from dataclasses import asdict
from decimal import Decimal

from constants import ChildResult

# Fallback variable names the snippet may have used for its answer, in order.
RESULT_VARIABLE_NAMES = ("result", "answer", "output")
ALLOWED_RESULT_TYPES = ("scalar", "table", "text")


class SafeEncoder(json.JSONEncoder):
    """Encoder for the value types the legal-case snippets are expected to emit.

    ``Decimal`` -> string: preserves exactness. Money must never round-trip
    through ``float``. ``date``/``datetime`` -> ISO string. ``set``/``tuple``
    -> list. Anything else falls back to ``repr`` so serialization of an
    unexpected object never hard-fails the run.
    """

    def default(self, o):  # noqa: D102
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if isinstance(o, (set, frozenset, tuple)):
            return list(o)
        return repr(o)


def _infer_type(value: object) -> str:
    """Best-effort result-type inference when the snippet gives no hint."""
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], dict):
        return "table"
    if isinstance(value, bool):
        return "text"
    if isinstance(value, (int, float, Decimal)):
        return "scalar"
    return "text"


def _resolve_result(namespace: dict, baseline_keys: set) -> tuple:
    """Resolve the snippet's answer using a forgiving fallback order.

    Returns ``(value, result_type, source_name)`` or ``(None, None, None)``.
    """
    for name in RESULT_VARIABLE_NAMES:
        if namespace.get(name) is not None:
            return namespace[name], _infer_type(namespace[name]), name
    # Exactly one new, non-dunder, non-callable binding -> treat it as the answer.
    new_keys = [
        k for k, v in namespace.items()
        if k not in baseline_keys and not k.startswith("__") and not callable(v)
    ]
    if len(new_keys) == 1:
        value = namespace[new_keys[0]]
        return value, _infer_type(value), new_keys[0]
    return None, None, None


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, result: ChildResult) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(result), fh, cls=SafeEncoder)


def run(snippet_path: str, cases_path: str, out_path: str) -> None:
    """Execute the snippet and write the result document to ``out_path``."""
    outcome = ChildResult()

    # Injected data comes from the trusted host; this guard is purely defensive.
    try:
        cases = json.loads(_read(cases_path))
    except Exception as exc:  # noqa: BLE001
        outcome.status = "runtime_error"
        outcome.error = f"could not load injected data: {exc}"
        _write(out_path, outcome)
        return

    snippet_src = _read(snippet_path)

    # Compile first so a malformed snippet is a distinct, clearly labelled
    # status. ValueError covers source containing null bytes -- a plausible
    # LLM output -- which compile() rejects with ValueError, not SyntaxError.
    try:
        code_obj = compile(snippet_src, "<snippet>", "exec")
    except (SyntaxError, ValueError) as exc:
        outcome.status = "syntax_error"
        outcome.error = str(exc)
        outcome.traceback = traceback.format_exc()
        _write(out_path, outcome)
        return

    namespace: dict = {"cases": cases, "__name__": "__sandbox__"}
    baseline_keys = set(namespace.keys())

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_buf, stderr_buf
    try:
        exec(code_obj, namespace)  # noqa: S102 - the container IS the sandbox
        value, result_type, source = _resolve_result(namespace, baseline_keys)
        if value is None:
            outcome.status = "no_result"
        else:
            hint = namespace.get("result_type")
            if hint in ALLOWED_RESULT_TYPES:
                result_type = hint
            outcome.status = "ok"
            outcome.result = value
            outcome.result_type = result_type
            outcome.result_source = source
    except BaseException as exc:  # noqa: BLE001 - report every snippet failure
        outcome.status = "runtime_error"
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.traceback = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        outcome.stdout = stdout_buf.getvalue()
        outcome.stderr = stderr_buf.getvalue()

    _write(out_path, outcome)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3])
