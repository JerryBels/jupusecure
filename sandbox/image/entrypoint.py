"""Trusted PARENT process inside the sandbox container (PID 1).

The untrusted snippet never runs here. This process spawns ``child_runner.py``
in a separate child process, reads the result document the child writes, and
emits exactly one canonical envelope on the container's real stdout.

Because the snippet runs in a different process, it has no handle to THIS
process's stdout, so it cannot forge the envelope via the container's stdout
channel. It can, however, write its own child-result file (code in a process
can write any descriptor that process holds) -- so the envelope's status and
result are NOT an independent attestation. The trustworthy, unforgeable
signals are host-observed: the container exit code, the OOM-killed flag, and
the wall-clock timeout. See docs/ADR.md section 4 for
the precise integrity claim.
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from dataclasses import asdict

# The wire-protocol contract (sentinel + envelope/child-result dataclasses
# + the tolerant parse_wire helper) is owned by ``constants.py`` (also baked
# into the image at /sandbox-app/) so the host runner and this in-container
# parent share a single source of truth. The /sandbox/* paths must match what
# the runner stages and mounts (sandbox/runner.py: _stage_inputs +
# config.CONTAINER_INPUT_DIR).
from constants import SANDBOX_ENVELOPE_SENTINEL as SENTINEL
from constants import ChildResult, SandboxEnvelope, parse_wire

SNIPPET_PATH = "/sandbox/snippet.py"        # read-only mount, injected per run
CASES_PATH = "/sandbox/cases.json"          # read-only mount, injected per run
CHILD_OUT_PATH = "/tmp/child_out.json"      # tmpfs, written by the child
CHILD_RUNNER = "/sandbox-app/child_runner.py"

RESULT_CAP_BYTES = 256 * 1024


def _truncate(text: str | None, cap: int) -> tuple[str, bool]:
    """Return ``(text, was_truncated)`` capped to ``cap`` UTF-8 bytes."""
    if not text:
        return "", False
    data = text.encode("utf-8", "replace")
    if len(data) <= cap:
        return text, False
    return data[:cap].decode("utf-8", "replace"), True


def main(snippet_path: str = SNIPPET_PATH,
         cases_path: str = CASES_PATH,
         child_out_path: str = CHILD_OUT_PATH,
         child_runner: str = CHILD_RUNNER) -> None:
    """Run the snippet via the child process and emit the envelope on stdout.

    The paths are parameters (defaulting to the in-container constants) purely
    so the full parent->child contract flow can be exercised by host tests
    without Docker. In the image, ``__main__`` calls this with the defaults.
    """
    envelope = SandboxEnvelope()
    try:
        proc = subprocess.run(
            [sys.executable, child_runner, snippet_path, cases_path, child_out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            with open(child_out_path, "r", encoding="utf-8") as fh:
                child = parse_wire(ChildResult, json.load(fh))
        except (OSError, json.JSONDecodeError):
            # No parseable result document: the child crashed hard or called
            # os._exit(). The run still gets a structured envelope.
            envelope.status = "no_result"
            envelope.error = "the sandboxed code did not produce a result"
            envelope.stderr = proc.stderr.decode(
                "utf-8", "replace")[:RESULT_CAP_BYTES]
        else:
            stdout, stdout_truncated = _truncate(child.stdout, RESULT_CAP_BYTES)
            result_json = json.dumps(child.result)
            _, result_truncated = _truncate(result_json, RESULT_CAP_BYTES)
            envelope.status = child.status or "envelope_error"
            envelope.result = None if result_truncated else child.result
            envelope.result_type = child.result_type
            envelope.result_source = child.result_source
            envelope.stdout = stdout
            envelope.stderr = child.stderr
            envelope.error = child.error
            envelope.traceback = child.traceback
            envelope.truncated = stdout_truncated or result_truncated
            if result_truncated:
                envelope.status = "output_too_large"
                envelope.error = "result exceeded the size cap"
    except Exception as exc:  # noqa: BLE001 - the parent must always emit one envelope
        envelope = SandboxEnvelope(status="envelope_error", error=str(exc),
                                   traceback=traceback.format_exc())

    # The single line on the container's real stdout. Nothing else writes here.
    sys.stdout.write(SENTINEL + json.dumps(asdict(envelope)) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
