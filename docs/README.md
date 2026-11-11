# Landmark3D engineering plans

These documents are the implementation authority for taking Landmark3D from recognisable blockouts to production-quality architectural GLB assets.

Read them in this order:

1. [`DETAIL_ENGINE_V2_PLAN.md`](DETAIL_ENGINE_V2_PLAN.md) — target architecture, data contracts, work breakdown, dependencies, and acceptance criteria.
2. [`PRODUCTION_BASELINE.md`](PRODUCTION_BASELINE.md) — product quality tiers, release gates, benchmark protocol, reliability requirements, and definition of production-ready.

Status: [`M0_M8_STATUS.md`](M0_M8_STATUS.md) is the current milestone report, including the 2026-11-10 flag-on
benchmark. [`M2_M5_RUN_REPORT.md`](M2_M5_RUN_REPORT.md) is an earlier report kept for history.
[`SMART_TEXTURE_PLAN.md`](SMART_TEXTURE_PLAN.md) is the plan for camera-calibrated, projective texturing (T0–T1
done, T2 built; progress in its section 8). [`SESSION_LOG.md`](SESSION_LOG.md) records where each work session
stopped and what to do next. [`FLOW_AUDIT_2026-11-11.md`](FLOW_AUDIT_2026-11-11.md) audits the business flow
after generated textures and plans the restructuring (P1–P12).

Rules for implementation agents:

- Treat the two documents above as requirements. If code and docs disagree, stop and record the conflict before changing the contract.
- Implement one vertical slice at a time, including schema, generator, validation, tests, and benchmark evidence.
- Do not add landmark-specific branches. Add reusable style families, semantic components, and deterministic constraints.
- Do not use triangle count as a proxy for quality. Each change must improve a named visual or structural metric.
- Preserve compatibility with existing version-1 specs until a documented migration removes it.
- Update these documents when an accepted architectural decision changes a contract or gate.

