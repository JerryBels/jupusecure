"""Host-side sandbox runner.

Runs one untrusted Python snippet per call inside a fresh, hardened, ephemeral
Docker container and classifies the outcome into one of five buckets. This is
the security boundary: the snippet runs with no network, a read-only root
filesystem, dropped capabilities, a non-root user, and cgroup memory/PID/CPU
limits, and the container is destroyed after every run.

The runner -- trusted host code the snippet can never reach -- decides the
outcome bucket from the container's exit state and the envelope emitted by the
in-container parent process. See docs/ADR.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.errors import APIError as DockerAPIError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

import config
# Single source of truth for the wire-protocol (sentinel + envelope dataclass).
# The same file is baked into the sandbox image (see sandbox/image/Dockerfile)
# so the in-container parent and this host runner share one definition; drift
# between two hand-maintained copies is impossible by construction.
from sandbox.image.constants import (SANDBOX_ENVELOPE_SENTINEL,
                                     SandboxEnvelope, parse_wire)

_SANDBOX_LABEL = {"jupus.sandbox": "1"}

# --- Outcome buckets ---------------------------------------------------------
BUCKET_OK = "ok"
BUCKET_RETRYABLE_CODE_ERROR = "retryable_code_error"
BUCKET_RESOURCE_EXCEEDED = "resource_exceeded"
BUCKET_INFRA_ERROR = "infra_error"
# BUCKET_RETRY_EXHAUSTED is owned by the orchestrator, not the runner.

# Envelope statuses the in-container parent can report for a run that executed.
_CODE_ERROR_STATUSES = frozenset(
    {"syntax_error", "runtime_error", "no_result", "envelope_error", "output_too_large"}
)


# session_id / execution_id are interpolated into host filesystem paths and
# Docker container names. They MUST be opaque identifiers, never user-derived
# text. The pattern matches what ``new_turn_id`` / ``new_execution_id`` produce
# and rules out ``..``, ``/``, and anything else that could traverse out of
# the runs directory.
_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass
class ExecutionRequest:
    """One snippet to execute. Safe to construct concurrently across sessions."""

    code: str
    cases_json: str
    session_id: str
    execution_id: str
    purpose: str = ""

    def __post_init__(self) -> None:
        for label, value in (("session_id", self.session_id),
                             ("execution_id", self.execution_id)):
            if not _ID_PATTERN.fullmatch(value):
                raise ValueError(
                    f"{label}={value!r} is not a safe identifier; "
                    "must match [a-zA-Z0-9_-]{1,64}"
                )


@dataclass
class ExecutionResult:
    """Trusted, host-decided outcome of a single sandbox run."""

    bucket: str
    sub_reason: str
    status: str | None = None
    result: object = None
    result_type: str | None = None
    result_source: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str | None = None
    exit_code: int | None = None
    oom_killed: bool = False
    timed_out: bool = False
    truncated: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.bucket == BUCKET_OK


def _looks_like_oom(failure_text: str) -> bool:
    return "MemoryError" in failure_text


def _looks_like_pid_exhaustion(failure_text: str) -> bool:
    signatures = (
        "BlockingIOError",
        "Resource temporarily unavailable",
        "can't start new thread",
        "Cannot allocate memory",
    )
    return any(signature in failure_text for signature in signatures)


class Runner:
    """Builds, runs, classifies, and tears down sandbox containers."""

    def __init__(self) -> None:
        # docker.from_env() connects eagerly (it auto-detects the API version),
        # so it raises if the daemon is down. Catching the failure here keeps
        # the constructor safe: scripts and the UI can build a Runner without
        # Docker and surface a clean message instead of a stack trace.
        try:
            self._client = docker.from_env()
        except DockerException:
            self._client = None

    def __enter__(self) -> "Runner":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        # Safety net. The Pythonic primary is the context manager above;
        # __del__ catches callers that forget. Don't remove it.
        self.close()

    def close(self) -> None:
        """Close the Docker client session."""
        if hasattr(self, "_client") and self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # -- lifecycle ------------------------------------------------------------

    def ping(self) -> bool:
        """Return True if the Docker daemon is reachable."""
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except DockerException:
            return False

    def reap_orphans(self) -> int:
        """Force-remove any sandbox containers left behind by a prior crash."""
        if self._client is None:
            return 0
        removed = 0
        try:
            stale = self._client.containers.list(
                all=True, filters={"label": "jupus.sandbox=1"}
            )
        except DockerException:
            return 0
        for container in stale:
            try:
                container.remove(force=True)
                removed += 1
            except DockerException:
                pass
        return removed

    # -- execution ------------------------------------------------------------
    def run(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute one snippet and return the trusted, classified outcome."""
        start_monotonic = time.monotonic()
        if self._client is None:
            return self._infra_error(
                "Docker is not available -- start Docker Desktop and retry.",
                start_monotonic,
            )
        run_dir = (config.SANDBOX_RUNS_DIR / request.execution_id).resolve()
        container = None
        try:
            self._stage_inputs(run_dir, request)
            try:
                container = self._create_container(run_dir, request)
            except ImageNotFound:
                return self._infra_error(
                    "sandbox image not found -- build it: "
                    "docker build -t jupus-sandbox sandbox/image",
                    start_monotonic,
                )
            except (DockerAPIError, DockerException) as exc:
                return self._infra_error(
                    f"could not create sandbox container: {exc}", start_monotonic)

            container.start()
            exit_code, timed_out = self._wait_for_exit(container)
            duration_ms = int((time.monotonic() - start_monotonic) * 1000)

            exit_code, oom_killed = self._inspect(container, exit_code)
            stdout = self._read_logs(container, stdout=True)
            stderr = self._read_logs(container, stdout=False)
            envelope = self._parse_envelope(stdout)

            return self._classify(
                envelope=envelope, exit_code=exit_code, oom_killed=oom_killed,
                timed_out=timed_out, duration_ms=duration_ms, stderr=stderr,
            )
        except DockerException as exc:
            return self._infra_error(
                f"docker error during execution: {exc}", start_monotonic)
        finally:
            self._cleanup(container, run_dir)

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _stage_inputs(run_dir: Path, request: ExecutionRequest) -> None:
        """Write the per-run input files that get bind-mounted read-only.

        cases.json contains client data. The shared parent directory is made
        owner-only (0700) so no other host user can traverse into any run's
        staging files -- the per-run dir itself stays group/other-readable so
        the container's non-root uid can still read the mounted inputs (it
        cannot match the host file owner). The staging dir is deleted in
        _cleanup once the run finishes.
        """
        config.SANDBOX_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(config.SANDBOX_RUNS_DIR, 0o700)
        run_dir.mkdir(parents=True, exist_ok=True)
        # Filenames must match the /sandbox/* paths read by entrypoint.py.
        (run_dir / "snippet.py").write_text(request.code, encoding="utf-8")
        (run_dir / "cases.json").write_text(request.cases_json, encoding="utf-8")

    def _create_container(self, run_dir: Path, request: ExecutionRequest):
        """Create a hardened, not-yet-started container. No LLM-controlled value
        reaches any parameter here except the bind-mounted snippet/data files."""
        return self._client.containers.create(
            image=config.SANDBOX_IMAGE,
            name=f"jupus-sbx-{request.session_id}-{request.execution_id}",
            labels=_SANDBOX_LABEL,
            # --- isolation ---
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=" + config.TMPFS_SIZE + ",mode=1777"},
            volumes={str(run_dir): {"bind": config.CONTAINER_INPUT_DIR, "mode": "ro"}},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            user="65532:65532",
            # --- resource limits ---
            pids_limit=config.PIDS_LIMIT,
            mem_limit=config.MEM_LIMIT,
            memswap_limit=config.MEM_LIMIT,  # == mem_limit disables swap
            nano_cpus=config.NANO_CPUS,
            ulimits=[
                docker.types.Ulimit(name="nofile", soft=config.ULIMIT_NOFILE,
                                    hard=config.ULIMIT_NOFILE),
                docker.types.Ulimit(name="nproc", soft=config.ULIMIT_NPROC,
                                    hard=config.ULIMIT_NPROC),
                docker.types.Ulimit(name="fsize", soft=config.ULIMIT_FSIZE,
                                    hard=config.ULIMIT_FSIZE),
            ],
            detach=True,
        )

    @staticmethod
    def _wait_for_exit(container) -> tuple[int | None, bool]:
        """Wait up to the wall-clock limit; kill and report on timeout."""
        try:
            outcome = container.wait(timeout=config.WALL_CLOCK_TIMEOUT_SECONDS)
            return outcome.get("StatusCode"), False
        except (ReadTimeout, RequestsConnectionError):
            try:
                container.kill()
            except DockerException:
                pass
            return None, True

    @staticmethod
    def _inspect(container, exit_code: int | None) -> tuple[int | None, bool]:
        """Read the OOM-killed flag and a fallback exit code from container state."""
        try:
            container.reload()
            state = container.attrs.get("State", {})
            oom = bool(state.get("OOMKilled"))
            if exit_code is None:
                exit_code = state.get("ExitCode")
            return exit_code, oom
        except DockerException:
            return exit_code, False

    @staticmethod
    def _read_logs(container, *, stdout: bool) -> str:
        try:
            log_bytes = container.logs(stdout=stdout, stderr=not stdout)
        except DockerException:
            return ""
        return log_bytes[: config.RUNNER_LOG_CAP_BYTES].decode("utf-8", "replace")

    @staticmethod
    def _parse_envelope(stdout: str) -> SandboxEnvelope | None:
        """Extract the canonical envelope. Only the trusted in-container parent
        writes to the container's stdout, so at most one sentinel line exists;
        a snippet's forged sentinel line is captured inside the child's output
        and never reaches here."""
        envelope = None
        for line in stdout.splitlines():
            if line.startswith(SANDBOX_ENVELOPE_SENTINEL):
                try:
                    parsed = json.loads(line[len(SANDBOX_ENVELOPE_SENTINEL):])
                except json.JSONDecodeError:
                    continue
                envelope = parse_wire(SandboxEnvelope, parsed)
        return envelope

    @staticmethod
    def _classify(*, envelope: SandboxEnvelope | None, exit_code: int | None,
                  oom_killed: bool, timed_out: bool, duration_ms: int,
                  stderr: str) -> ExecutionResult:
        """Decide the outcome bucket. Trusted: independent of snippet integrity."""
        if timed_out:
            return ExecutionResult(
                bucket=BUCKET_RESOURCE_EXCEEDED, sub_reason="timeout",
                error=f"execution exceeded the {config.WALL_CLOCK_TIMEOUT_SECONDS}s "
                      "wall-clock limit",
                exit_code=exit_code, timed_out=True, duration_ms=duration_ms,
            )
        if oom_killed:
            return ExecutionResult(
                bucket=BUCKET_RESOURCE_EXCEEDED, sub_reason="out_of_memory",
                error="execution exceeded the memory limit",
                exit_code=exit_code, oom_killed=True, duration_ms=duration_ms,
            )
        if envelope is None:
            sub_reason = "killed" if exit_code == 137 else "no_envelope"
            bucket = BUCKET_RESOURCE_EXCEEDED if exit_code == 137 else BUCKET_INFRA_ERROR
            return ExecutionResult(
                bucket=bucket, sub_reason=sub_reason,
                error="the sandbox produced no result envelope",
                exit_code=exit_code, stderr=stderr, duration_ms=duration_ms,
            )

        envelope_fields = dict(
            status=envelope.status, result=envelope.result,
            result_type=envelope.result_type,
            result_source=envelope.result_source,
            stdout=envelope.stdout or "",
            stderr=envelope.stderr or stderr,
            error=envelope.error, traceback=envelope.traceback,
            exit_code=exit_code, truncated=bool(envelope.truncated),
            duration_ms=duration_ms,
        )
        if envelope.status == "ok":
            return ExecutionResult(bucket=BUCKET_OK, sub_reason="ok",
                                   **envelope_fields)
        if envelope.status in _CODE_ERROR_STATUSES:
            failure_text = (
                (envelope.error or "")
                + (envelope.traceback or "")
                + (envelope.stderr or "")
                + stderr
            )
            if _looks_like_oom(failure_text):
                return ExecutionResult(bucket=BUCKET_RESOURCE_EXCEEDED,
                                       sub_reason="out_of_memory", **envelope_fields)
            if _looks_like_pid_exhaustion(failure_text):
                return ExecutionResult(bucket=BUCKET_RESOURCE_EXCEEDED,
                                       sub_reason="pid_limit", **envelope_fields)
            return ExecutionResult(bucket=BUCKET_RETRYABLE_CODE_ERROR,
                                   sub_reason=envelope.status, **envelope_fields)
        # Unknown status -> retryable, surfaced honestly.
        return ExecutionResult(bucket=BUCKET_RETRYABLE_CODE_ERROR,
                               sub_reason="unknown_status", **envelope_fields)

    @staticmethod
    def _infra_error(message: str, start_monotonic: float) -> ExecutionResult:
        return ExecutionResult(
            bucket=BUCKET_INFRA_ERROR, sub_reason="docker_unavailable",
            error=message,
            duration_ms=int((time.monotonic() - start_monotonic) * 1000),
        )

    @staticmethod
    def _cleanup(container, run_dir: Path) -> None:
        if container is not None:
            try:
                container.remove(force=True)
            except (DockerException, NotFound):
                pass
        shutil.rmtree(run_dir, ignore_errors=True)
