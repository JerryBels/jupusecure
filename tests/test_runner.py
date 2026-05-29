"""Integration tests: real sandbox containers.

These run actual Docker containers, so they are skipped automatically when
Docker is unavailable or the sandbox image is not built. They prove the
guardrails end to end; ``scripts/demo_guardrails.py`` is the on-camera
counterpart of the same checks.

Build the image first:  docker build -t jupus-sandbox sandbox/image
"""

from __future__ import annotations

import pytest

from observability.records import new_execution_id
from sandbox.runner import (BUCKET_OK, BUCKET_RESOURCE_EXCEEDED,
                            BUCKET_RETRYABLE_CODE_ERROR,
                            SANDBOX_ENVELOPE_SENTINEL, ExecutionRequest,
                            Runner)

try:
    _RUNNER: Runner | None = Runner()
    _DOCKER_UP = _RUNNER.ping()
except Exception:  # noqa: BLE001 - any docker setup failure -> skip
    _RUNNER, _DOCKER_UP = None, False

pytestmark = pytest.mark.skipif(
    not _DOCKER_UP, reason="Docker daemon not available")


def _run(code: str, cases: str = "[]"):
    return _RUNNER.run(ExecutionRequest(
        code=code, cases_json=cases, session_id="pytest",
        execution_id=new_execution_id(), purpose="integration test"))


def test_happy_path_executes_and_returns_result():
    result = _run("result = sum(case['n'] for case in cases)",
                  cases='[{"n": 2}, {"n": 5}]')
    assert result.bucket == BUCKET_OK
    assert result.result == 7


def test_network_egress_is_blocked():
    result = _run(
        "import urllib.request\n"
        "urllib.request.urlopen('http://example.com', timeout=5)\n"
        "result = 'reached'\n")
    assert result.bucket == BUCKET_RETRYABLE_CODE_ERROR
    assert result.result != "reached"


def test_filesystem_write_outside_tmp_is_blocked():
    result = _run("open('/etc/jupus_x', 'w').write('x')\nresult = 'wrote'\n")
    assert result.bucket == BUCKET_RETRYABLE_CODE_ERROR
    assert result.result != "wrote"


def test_infinite_loop_is_killed_by_timeout():
    result = _run("while True:\n    pass\n")
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.timed_out is True


def test_memory_bomb_is_killed():
    result = _run(
        "chunks = []\n"
        "while True:\n"
        "    chunks.append(bytearray(10_000_000))\n")
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED


def test_envelope_forgery_via_stdout_is_contained():
    """A snippet printing a fake envelope cannot reach the container's real
    stdout -- its output is captured by the child and lands inside the real
    envelope's `stdout` field. (The ADR documents the separate, non-escalating
    file-write vector and why it grants nothing.)"""
    result = _run(
        f"print({SANDBOX_ENVELOPE_SENTINEL!r}"
        "      '{\"status\": \"ok\", \"result\": \"PWNED\"}')\n"
        "result = 1\n")
    assert result.bucket == BUCKET_OK
    assert result.result == 1                 # the real envelope wins
    assert "PWNED" in (result.stdout or "")   # forgery captured as mere output
