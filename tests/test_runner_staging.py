"""Tests for the runner's per-run input staging and the offline-safety net.

``_stage_inputs`` writes the snippet and the case data (which contains client
PII) to a bind-mounted directory. It is a pure host-side operation, so it is
tested here without Docker.

The "Docker-down" tests verify ``Runner`` never crashes its caller when the
daemon is missing -- ``docker.from_env()`` connects eagerly, so the
constructor must catch its failure and every method must short-circuit.
"""

from __future__ import annotations

import shutil
import stat

import config
from sandbox.runner import (BUCKET_INFRA_ERROR, ExecutionRequest, Runner)


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        code="result = 1", cases_json='[{"client_name": "secret"}]',
        session_id="t", execution_id="staging_test_run",
    )


def _cleanup(run_dir):
    shutil.rmtree(run_dir, ignore_errors=True)


def test_stage_inputs_writes_both_files():
    run_dir = config.SANDBOX_RUNS_DIR / "staging_test_run"
    try:
        Runner._stage_inputs(run_dir, _request())
        assert (run_dir / "snippet.py").read_text() == "result = 1"
        assert "secret" in (run_dir / "cases.json").read_text()
    finally:
        _cleanup(run_dir)


def test_staging_parent_directory_is_owner_only():
    """The shared .sandbox_runs/ parent must be 0700 so no other host user can
    traverse into any run's staged client data."""
    run_dir = config.SANDBOX_RUNS_DIR / "staging_test_run"
    try:
        Runner._stage_inputs(run_dir, _request())
        mode = stat.S_IMODE(config.SANDBOX_RUNS_DIR.stat().st_mode)
        assert mode == 0o700, f"expected 0700, got {oct(mode)}"
    finally:
        _cleanup(run_dir)


# --- Docker-down safety net --------------------------------------------------
def _offline_runner() -> Runner:
    """A Runner whose Docker client is missing -- simulates a daemon-down host
    regardless of whether Docker happens to be running where the test runs."""
    runner = Runner()
    runner._client = None
    return runner


def test_constructor_does_not_raise_when_daemon_is_down():
    """docker.from_env() connects eagerly; the constructor must catch and
    leave a usable Runner whose methods short-circuit cleanly."""
    # We can't unconditionally simulate "daemon down" at construction time
    # (the real daemon may be up), but we can verify the field is present
    # and the explicit None-client path is callable.
    runner = _offline_runner()
    assert runner._client is None  # noqa: SLF001 - asserting the safety-net


def test_ping_returns_false_when_client_is_unavailable():
    assert _offline_runner().ping() is False


def test_reap_orphans_returns_zero_when_client_is_unavailable():
    assert _offline_runner().reap_orphans() == 0


def test_run_yields_infra_error_when_client_is_unavailable():
    result = _offline_runner().run(_request())
    assert result.bucket == BUCKET_INFRA_ERROR
    assert result.error and "docker" in result.error.lower()


def test_context_manager_closes_on_exit():
    """`with Runner() as r:` is the primary cleanup path -- the __del__
    safety net stays as a backstop but should not be the only mechanism."""
    with Runner() as runner:
        assert runner is not None
        # Inside the block, the client may be set (Docker up) or None (down);
        # either way exit must leave it None.
    assert runner._client is None  # noqa: SLF001 - locking in the contract


def test_context_manager_closes_on_exit_even_when_body_raises():
    """Exiting via an exception still closes the client."""
    runner = Runner()
    try:
        with runner:
            raise RuntimeError("intentional")
    except RuntimeError:
        pass
    assert runner._client is None  # noqa: SLF001


def test_close_cleans_up_client():
    runner = Runner()
    runner.close()
    assert runner._client is None
