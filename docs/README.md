# Landmark3D engineering plans

These documents are the implementation authority for taking Landmark3D from recognisable blockouts to production-quality architectural GLB assets.

Read them in this order:

1. [`DETAIL_ENGINE_V2_PLAN.md`](DETAIL_ENGINE_V2_PLAN.md) — target architecture, data contracts, work breakdown, dependencies, and acceptance criteria.
2. [`PRODUCTION_BASELINE.md`](PRODUCTION_BASELINE.md) — product quality tiers, release gates, benchmark protocol, reliability requirements, and definition of production-ready.

Rules for implementation agents:

- Treat the two documents above as requirements. If code and docs disagree, stop and record the conflict before changing the contract.
- Implement one vertical slice at a time, including schema, generator, validation, tests, and benchmark evidence.
- Do not add landmark-specific branches. Add reusable style families, semantic components, and deterministic constraints.
- Do not use triangle count as a proxy for quality. Each change must improve a named visual or structural metric.
- Preserve compatibility with existing version-1 specs until a documented migration removes it.
- Update these documents when an accepted architectural decision changes a contract or gate.

