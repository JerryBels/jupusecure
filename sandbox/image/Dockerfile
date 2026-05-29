# Sandbox image for executing untrusted, LLM-generated Python.
#
# Stdlib-only: no third-party packages are installed, which keeps the attack
# surface minimal and the image fully functional offline (the container runs
# with --network none).
#
# Production note: pin the base image by sha256 digest
# (FROM python:3.12-slim@sha256:...) and build from a trusted registry mirror.
# A mutable tag is used here so the POC builds offline without a pre-fetched
# digest.
FROM python:3.12-slim

# Non-root system user. Numeric UID/GID so it resolves even though the
# container runs with a read-only root filesystem and no writable /etc.
RUN groupadd --system --gid 65532 sandbox \
 && useradd  --system --uid 65532 --gid sandbox \
             --no-create-home --shell /usr/sbin/nologin sandbox

# Trusted sandbox application code (parent entrypoint + child runner + the
# wire-protocol constants shared with the host runner). Owned by root,
# world-readable; the untrusted snippet is injected separately at run time as
# a read-only bind mount at /sandbox.
COPY entrypoint.py child_runner.py constants.py /sandbox-app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:65532
WORKDIR /tmp

ENTRYPOINT ["python", "/sandbox-app/entrypoint.py"]
