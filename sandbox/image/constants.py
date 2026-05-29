"""Shared wire-protocol contract for the host runner and the in-container parent.

This file is the contract between two physically separated processes: the host
``sandbox/runner.py`` that reads the container's stdout, and the in-container
``entrypoint.py`` that writes to it. Keeping a single definition here removes
the drift-by-edit risk that two hand-maintained copies would carry.

Constraint: this module is baked into the sandbox image alongside
``entrypoint.py`` and ``child_runner.py``. It MUST stay stdlib-only and free
of any project import -- the image is built standalone (no project sources,
no third-party packages beyond what's in ``python:3.12-slim``).
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# Prefix of the single envelope line the trusted in-container parent writes
# to the container's real stdout. The host scans stdout for a line starting
# with this string and parses the JSON that follows.
SANDBOX_ENVELOPE_SENTINEL = "__JUPUS_SANDBOX_ENVELOPE__"


@dataclass
class ChildResult:
    """What the untrusted child writes to its result file for the parent.

    Crosses one boundary (child process -> parent process, via tmpfs file).
    Built by ``child_runner.py``, consumed by ``entrypoint.py``. Serialize
    with ``dataclasses.asdict``; parse with ``parse_wire``.
    """

    status: str | None = None
    result: object = None
    result_type: str | None = None
    result_source: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str | None = None


@dataclass
class SandboxEnvelope:
    """The one canonical document the parent emits on the container's stdout.

    Crosses the second boundary (container -> host, via Docker logs). The host
    ``Runner`` parses this from the sentinel-prefixed line. Serialize with
    ``dataclasses.asdict``; parse with ``parse_wire``.
    """

    status: str = "envelope_error"
    result: object = None
    result_type: str | None = None
    result_source: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str | None = None
    truncated: bool = False


def parse_wire(cls, data: dict | None):
    """Build a wire dataclass tolerantly from a parsed-JSON dict.

    Shared by ``ChildResult`` and ``SandboxEnvelope`` -- the two messages that
    round-trip through JSON across a process boundary. Unknown keys are
    ignored and missing ones fall back to the dataclass defaults, so an
    image/host built from a slightly different revision still parses.
    """
    known = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in (data or {}).items()
                  if key in known})
