"""Central configuration for the secure code-execution POC.

Every component imports from here so limits and names live in one place.
Values are read once at import time; runtime-tunable values come from the
environment (see .env.example).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
SANDBOX_RUNS_DIR = PROJECT_ROOT / ".sandbox_runs"  # per-run mounted input dirs
LOGS_DIR = PROJECT_ROOT / "logs"
CASES_DB_PATH = PROJECT_ROOT / "data" / "cases.db"
EXECUTION_LOG_PATH = LOGS_DIR / "executions.jsonl"

# --- LLM ---------------------------------------------------------------------
# Provider is currently Gemini. Adding another provider is a one-file change
# (sibling of agent/llm_gemini.py implementing agent.llm.LLMClient) plus a
# swap of one factory line in agent/orchestrator.py.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# --- Sandbox image -----------------------------------------------------------
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "jupus-sandbox:latest")

# --- Sandbox resource limits -------------------------------------------------
# Hardening baseline. See the ADR for the rationale behind each value.
WALL_CLOCK_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_WALL_CLOCK_TIMEOUT", "15"))
MEM_LIMIT = "256m"
PIDS_LIMIT = 16
NANO_CPUS = 1_000_000_000  # 1.0 CPU
TMPFS_SIZE = "64m"
ULIMIT_NOFILE = 256
ULIMIT_NPROC = 64
ULIMIT_FSIZE = 8 * 1024 * 1024  # 8 MiB max file size inside the sandbox

# --- Output caps -------------------------------------------------------------
# How much container log output the runner reads back. (The in-container
# result/stdout cap lives in sandbox/image/entrypoint.py, which is built
# standalone into the image and cannot import this module.)
RUNNER_LOG_CAP_BYTES = 1 * 1024 * 1024

# --- Agent loop --------------------------------------------------------------
MAX_TOOL_ITERATIONS = 2  # bounded retry budget per user turn

# --- Container paths ---------------------------------------------------------
# Mount point of the per-run input directory inside the sandbox. The snippet
# and cases filenames within it are owned by entrypoint.py (see _stage_inputs).
CONTAINER_INPUT_DIR = "/sandbox"
