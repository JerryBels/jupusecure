"""Tests for the in-sandbox child runner (the result-contract logic).

child_runner.py is pure stdlib and runs the untrusted snippet, so it can be
exercised directly on the host without Docker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_IMAGE_DIR = Path(__file__).resolve().parents[1] / "sandbox" / "image"
sys.path.insert(0, str(_IMAGE_DIR))

import child_runner  # noqa: E402

_CASES = '[{"id": 1, "claim_amount": "100.00"}, {"id": 2, "claim_amount": "250.50"}]'


def _run(tmp_path: Path, snippet: str, cases: str = _CASES) -> dict:
    (tmp_path / "snippet.py").write_text(snippet, encoding="utf-8")
    (tmp_path / "cases.json").write_text(cases, encoding="utf-8")
    out = tmp_path / "out.json"
    child_runner.run(str(tmp_path / "snippet.py"),
                     str(tmp_path / "cases.json"), str(out))
    return json.loads(out.read_text(encoding="utf-8"))


def test_happy_path_returns_ok_and_decimal_as_string(tmp_path):
    doc = _run(tmp_path,
               "from decimal import Decimal\n"
               "result = sum(Decimal(case['claim_amount']) for case in cases)\n")
    assert doc["status"] == "ok"
    assert doc["result"] == "350.50"        # Decimal serialized as string, exact
    assert doc["result_source"] == "result"


def test_answer_variable_is_a_valid_fallback(tmp_path):
    doc = _run(tmp_path, "answer = len(cases)\n")
    assert doc["status"] == "ok"
    assert doc["result"] == 2
    assert doc["result_source"] == "answer"


def test_single_new_binding_is_used_as_result(tmp_path):
    doc = _run(tmp_path, "grand_total = 7\n")
    assert doc["status"] == "ok"
    assert doc["result"] == 7
    assert doc["result_source"] == "grand_total"


def test_no_result_when_nothing_is_bound(tmp_path):
    doc = _run(tmp_path, "print('only a print')\n")
    assert doc["status"] == "no_result"
    assert doc["result"] is None
    assert "only a print" in doc["stdout"]


def test_syntax_error_is_a_distinct_status(tmp_path):
    doc = _run(tmp_path, "result = (1 +\n")
    assert doc["status"] == "syntax_error"
    assert doc["traceback"]


def test_source_with_a_null_byte_is_a_clean_status(tmp_path):
    """compile() rejects null bytes with ValueError, not SyntaxError -- it must
    still produce a clean status, not crash the child runner."""
    doc = _run(tmp_path, "result = 1\x00\n")
    assert doc["status"] == "syntax_error"
    assert doc["result"] is None


def test_runtime_error_is_captured_with_traceback(tmp_path):
    doc = _run(tmp_path, "result = cases[99]['missing']\n")
    assert doc["status"] == "runtime_error"
    assert "IndexError" in doc["error"]
    assert doc["traceback"]


def test_result_type_hint_is_respected_when_valid(tmp_path):
    doc = _run(tmp_path, "result = [{'a': 1}]\nresult_type = 'table'\n")
    assert doc["result_type"] == "table"


def test_invalid_result_type_hint_is_ignored(tmp_path):
    doc = _run(tmp_path, "result = 5\nresult_type = 'nonsense'\n")
    assert doc["result_type"] == "scalar"  # inferred, hint rejected


def test_snippet_sys_exit_is_caught_not_propagated(tmp_path):
    doc = _run(tmp_path, "import sys\nsys.exit(3)\n")
    assert doc["status"] == "runtime_error"  # SystemExit caught, structured


def test_snippet_stdout_is_captured(tmp_path):
    doc = _run(tmp_path, "print('hello from snippet')\nresult = 1\n")
    assert "hello from snippet" in doc["stdout"]
