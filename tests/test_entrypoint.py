"""Tests for the in-sandbox trusted parent process (entrypoint.py).

The parent spawns the child runner, reads its result document, and emits the
one canonical envelope. With ``main()`` parameterised, the whole parent->child
contract flow runs on the host without Docker -- including the proof that a
snippet cannot forge the envelope via stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_IMAGE_DIR = Path(__file__).resolve().parents[1] / "sandbox" / "image"
sys.path.insert(0, str(_IMAGE_DIR))

import entrypoint  # noqa: E402

_CHILD_RUNNER = str(_IMAGE_DIR / "child_runner.py")
_CASES = '[{"id": 1, "claim_amount": "100.00"}, {"id": 2, "claim_amount": "250.50"}]'


def _run(tmp_path: Path, snippet: str, capsys, cases: str = _CASES,
         child_runner: str = _CHILD_RUNNER) -> dict:
    (tmp_path / "snippet.py").write_text(snippet, encoding="utf-8")
    (tmp_path / "cases.json").write_text(cases, encoding="utf-8")
    entrypoint.main(str(tmp_path / "snippet.py"), str(tmp_path / "cases.json"),
                    str(tmp_path / "child_out.json"), child_runner)
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.startswith(entrypoint.SENTINEL):
            return json.loads(line[len(entrypoint.SENTINEL):])
    raise AssertionError(f"no envelope emitted on stdout: {out!r}")


def test_happy_path_emits_an_ok_envelope(tmp_path, capsys):
    envelope = _run(tmp_path,
               "from decimal import Decimal\n"
               "result = sum(Decimal(case['claim_amount']) for case in cases)\n",
               capsys)
    assert envelope["status"] == "ok"
    assert envelope["result"] == "350.50"


def test_runtime_error_is_reported_in_the_envelope(tmp_path, capsys):
    envelope = _run(tmp_path, "result = cases[99]['missing']\n", capsys)
    assert envelope["status"] == "runtime_error"
    assert "IndexError" in envelope["error"]


def test_syntax_error_is_reported_in_the_envelope(tmp_path, capsys):
    envelope = _run(tmp_path, "result = (1 +\n", capsys)
    assert envelope["status"] == "syntax_error"


def test_child_that_exits_hard_yields_a_no_result_envelope(tmp_path, capsys):
    """A snippet that os._exit()s kills the child before it writes its result
    document. The parent still emits a structured envelope."""
    envelope = _run(tmp_path, "import os\nos._exit(0)\n", capsys)
    assert envelope["status"] == "no_result"


def test_envelope_forgery_via_stdout_is_contained(tmp_path, capsys):
    """A snippet that prints a fake envelope cannot reach the parent's stdout:
    the child's output is captured and lands inside the real envelope."""
    envelope = _run(
        tmp_path,
        f"print({entrypoint.SENTINEL!r} + '{{\"status\": \"ok\", "
        f"\"result\": \"PWNED\"}}')\n"
        "result = 1\n",
        capsys)
    assert envelope["status"] == "ok"
    assert envelope["result"] == 1                      # the real envelope wins
    assert "PWNED" in envelope["stdout"]                # forgery captured as output


def test_oversized_result_is_capped(tmp_path, capsys):
    envelope = _run(tmp_path, "result = 'x' * (300 * 1024)\n", capsys)
    assert envelope["status"] == "output_too_large"
    assert envelope["result"] is None
    assert envelope["truncated"] is True
