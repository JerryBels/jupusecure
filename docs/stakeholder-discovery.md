# Stakeholder Discovery — Open Questions

Companion to [ADR](./ADR.md). The POC proceeded on
assumptions appropriate for a beta; these are the questions a real version of
this work resolves in the first week. The brief gives us a PM (1h/day), an AI
Lead (1h/afternoon), the Head of Engineering, and the existing chat-product
developers — this is what I'd take to each.

## Product Manager (1h/day)

- **Caseload scale per lawyer?** Dozens, hundreds, or millions of cases? Decides
  whether per-turn JSON injection scales at all or whether RAG retrieval is
  foundational from day one (currently deferred — see the seam in
  `data/repository.py`).
- **Queries per day, peak concurrency?** Drives sandbox sizing and cost
  projections.
- **Jurisdiction multiplicity?** The 30% provision rule is German; multi-tenant
  means multi-rule. Are computation rules per-lawyer / per-jurisdiction?

## AI Lead (1h/afternoon)

- **Model preference + budget per turn?** Drove the Gemini choice for the POC;
  production may prefer a self-hosted open-source model on EU infrastructure for
  privacy.
- **Existing prompt-engineering conventions** in the chat product — what should
  we mirror?
- **Retrieval-intent vs. retrieval-scope** — does the current chat product
  already separate LLM-controlled *what to look for* from server-controlled
  *what the user may see*? This is the security-critical line for agentic
  retrieval.

## Head of Engineering

- **Production runtime target** (Linux bare-metal? Lambda? ECS?).
- **Existing observability stack** (Datadog, others?) for trace/metric
  integration.
- **Security-review process** and when to engage it, with whom.
- **Data residency** (EU? US? Multi-region?) — drives provider choice and the
  self-hosted-model decision.

## Existing chat-product developers

- **Where does the chat live** in the Django app — view layer, separate service,
  ASGI?
- **Authentication shape** — session, JWT, OAuth?
- **Multi-tenant isolation pattern in Postgres today** — per-database,
  per-schema (`django-tenants`?), or shared table with RLS? Foundational: our
  RAG retrieval must sit on the same isolation primitive.
- **pgvector indices already in production?** Index type (HNSW vs IVFFlat),
  parameters, and per-tenant configuration if any.
- **Downstream-error conventions** — how does the existing chat degrade when a
  backend fails?