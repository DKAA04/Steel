# Roadmap

1. Done: offline note store with expiry and regression tests.
2. Done (Phase 1): packaging on a pinned OpenJarvis commit, `steel chat` with OpenJarvis automatic memory, forget-by-id and wipe across all memory stores, private file permissions. No tools.
3. Done (Phase 1b): user-only extraction, near-duplicate guard, related facts on forget, 7-day notes for time-bound facts, `steel remember`. Open: forget still blocks only the same wording; named people: decided to stay as stored, revisited when a tool can read memory (docs/MEMORY_QUALITY.md).
4. Done (Phase 2a): `SteelPolicy`, `build_executor`, the names-only decision log, `steel policy show`; designed (docs/POLICY.md) and tested **before** any tool is registered. Open: decide on wildcard breadth and legacy names (POLICY.md section 6).
4b. Phase 2b (next, not started): the first tool, read-only, behind the policy; revisit per-person handling of memory when it can read facts.
5. Voice and vision: define consent, visible recording indicators and retention before attaching sensors. Room observations build on the expiring note store.
6. Mobile/API: authentication and authorization must come before any remote command interface.

These are planned milestones with no promised dates. The security module will be limited to owned or explicitly authorized environments.
