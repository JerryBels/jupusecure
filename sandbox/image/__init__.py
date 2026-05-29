"""Files baked into the sandbox Docker image.

This directory plays a dual role: it is both a Docker build context
(see ``Dockerfile`` for the explicit COPY list) AND a Python sub-package
the host imports from (``sandbox/runner.py`` imports the wire-protocol
constants from ``sandbox.image.constants``).

Rules for anything added here:

  * Stay stdlib-only -- no third-party deps; the image only has what
    ``python:3.12-slim`` ships.
  * Inside this directory, modules MUST import each other unqualified
    (e.g. ``from constants import …``), not via the ``sandbox.image.…``
    package path. Inside the running container these files live flat
    in ``/sandbox-app/`` with no package; the bare import is what works
    in both worlds.
  * If you add a new module, add it to the ``COPY`` line in
    ``Dockerfile``. Files not listed there are invisible to the
    container at runtime, regardless of being importable on the host.
"""
