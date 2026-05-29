---
review_agents:
  - compound-engineering:review:security-sentinel
  - compound-engineering:review:architecture-strategist
  - compound-engineering:review:code-simplicity-reviewer
  - compound-engineering:review:agent-native-reviewer
---

# Project Context for /ce:review

**Project:** Jupus Caseload Assistant — a secure code-execution sandbox POC for a
Staff Engineer takehome. **Beta-quality**, no production deployment.
Single-developer codebase, ~80 logic tests + 6 Docker integration tests.

**Prior reviews to avoid duplicating:**

- `JUPUS_DIFFERENTIAL_REVIEW_2026-05-24.md` covered the diff up through commit
  `f2d7bdc`. Its real findings were: (a) unbounded conversation history
  (deferred), (b) stale data profile in cached client (deferred), (c) __del__
  fragility (FIXED — Runner is now a context manager). Don't re-raise these.
- The ADR (`docs/ADR.md`) names the deliberate beta
  trade-offs explicitly. Anything the ADR already acknowledges as a known
  limit should not be flagged again — flag only genuinely new defects.

**Bias the review toward:**

- Real defects that would block a Staff-Engineer reviewer's approval
- Subtle bugs in the recent session-client caching (state lives across
  Streamlit reruns; concurrent submit-while-running could be interesting)
- Anything that contradicts what the ADR / docstrings claim

**Don't flag:**

- Production hardening items already named in ADR §"Iteration Path"
- Streamlit-managed lifetimes (`@st.cache_resource` is the right call)
- The history-bloat / dynamic-prompt items already documented
