"""Tests for the runner's outcome classification.

``Runner._classify`` and ``Runner._parse_envelope`` are pure functions of the
container's exit state and stdout, so they are tested here without Docker.
This is where the OOM/timeout/PID-limit precedence and the envelope-forgery
defense are verified.
"""

from __future__ import annotations

from sandbox.image.constants import SandboxEnvelope
from sandbox.runner import (BUCKET_INFRA_ERROR, BUCKET_OK,
                            BUCKET_RESOURCE_EXCEEDED,
                            BUCKET_RETRYABLE_CODE_ERROR,
                            SANDBOX_ENVELOPE_SENTINEL, Runner)

_SENTINEL = SANDBOX_ENVELOPE_SENTINEL  # the protocol constant, not a hard-coded copy


def _classify(**kwargs):
    base = dict(envelope=None, exit_code=0, oom_killed=False,
                timed_out=False, duration_ms=10, stderr="")
    base.update(kwargs)
    return Runner._classify(**base)


def _envelope(status, error="", traceback="") -> SandboxEnvelope:
    return SandboxEnvelope(status=status, error=error, traceback=traceback)


# --- precedence: timeout / OOM win -------------------------------------------
def test_timeout_classified_as_resource_exceeded():
    result = _classify(timed_out=True, exit_code=None)
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "timeout"


def test_oom_classified_as_resource_exceeded():
    result = _classify(oom_killed=True, exit_code=137)
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "out_of_memory"


def test_timeout_wins_even_if_an_envelope_is_present():
    result = _classify(timed_out=True, envelope=_envelope("ok"))
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED


def test_oom_wins_over_a_forged_ok_envelope():
    """A snippet can write a forged `status: ok` envelope, but a host-observed
    OOM kill is decided outside its process and overrides it."""
    result = _classify(oom_killed=True, envelope=_envelope("ok"))
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "out_of_memory"


# --- missing envelope --------------------------------------------------------
def test_missing_envelope_with_137_is_resource_exceeded():
    result = _classify(envelope=None, exit_code=137)
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "killed"


def test_missing_envelope_with_other_exit_is_infra_error():
    result = _classify(envelope=None, exit_code=1)
    assert result.bucket == BUCKET_INFRA_ERROR


# --- envelope statuses -------------------------------------------------------
def test_ok_envelope_is_ok_bucket():
    result = _classify(envelope=_envelope("ok"))
    assert result.bucket == BUCKET_OK and result.ok


def test_runtime_error_is_retryable_code_error():
    result = _classify(envelope=_envelope("runtime_error", error="KeyError: x"))
    assert result.bucket == BUCKET_RETRYABLE_CODE_ERROR
    assert result.sub_reason == "runtime_error"


def test_syntax_error_is_retryable_code_error():
    result = _classify(envelope=_envelope("syntax_error"))
    assert result.bucket == BUCKET_RETRYABLE_CODE_ERROR


# --- heuristic reclassification of resource exhaustion -----------------------
def test_memoryerror_in_envelope_reclassified_as_oom():
    env = _envelope("runtime_error", error="MemoryError")
    result = _classify(envelope=env)
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "out_of_memory"


def test_thread_exhaustion_reclassified_as_pid_limit():
    env = _envelope("runtime_error",
                    traceback="RuntimeError: can't start new thread")
    result = _classify(envelope=env)
    assert result.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result.sub_reason == "pid_limit"


def test_resource_exhaustion_in_stderr_reclassified():
    # MemoryError in envelope stderr
    env1 = _envelope("no_result")
    env1.stderr = "MemoryError during execution"
    result1 = _classify(envelope=env1)
    assert result1.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result1.sub_reason == "out_of_memory"

    # Thread limit exhaustion in envelope stderr
    env2 = _envelope("no_result")
    env2.stderr = "RuntimeError: can't start new thread"
    result2 = _classify(envelope=env2)
    assert result2.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result2.sub_reason == "pid_limit"

    # Host-level stderr signature
    env3 = _envelope("no_result")
    result3 = _classify(envelope=env3,
                        stderr="BlockingIOError: Resource temporarily unavailable")
    assert result3.bucket == BUCKET_RESOURCE_EXCEEDED
    assert result3.sub_reason == "pid_limit"


# --- envelope parsing / forgery defense --------------------------------------
def test_parse_envelope_reads_the_sentinel_line():
    stdout = _SENTINEL + '{"status": "ok", "result": 5}\n'
    assert Runner._parse_envelope(stdout).result == 5


def test_parse_envelope_ignores_a_forged_line_and_takes_the_real_one():
    # A forged sentinel line would only ever appear BEFORE the trusted parent's
    # real line; the parser takes the last valid one.
    forged = _SENTINEL + '{"status": "ok", "result": "PWNED"}'
    real = _SENTINEL + '{"status": "ok", "result": "REAL"}'
    assert Runner._parse_envelope(forged + "\n" + real).result == "REAL"


def test_parse_envelope_returns_none_when_absent():
    assert Runner._parse_envelope("just some logs\n") is None
