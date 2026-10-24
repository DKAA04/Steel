# Roadmap

1. Done: offline note store with expiry and regression tests.
2. Done (Phase 1): packaging on a pinned OpenJarvis commit, `steel chat` with OpenJarvis automatic memory, forget-by-id and wipe across all memory stores, private file permissions. No tools.
3. Done (Phase 1b): user-only extraction, near-duplicate guard, related facts on forget, 7-day notes for time-bound facts, `steel remember`. Open: forget still blocks only the same wording; named people: decided to stay as stored, revisited when a tool can read memory (docs/MEMORY_QUALITY.md).
4. Done (Phase 2a): `SteelPolicy`, `build_executor`, the names-only decision log, `steel policy show`; designed (docs/POLICY.md) and tested **before** any tool existed.
4b. Done (Phase 2b): policy hardening (one grantable wildcard, `grant_moved`, log rotation, a test that `ToolExecutor.execute` is the only way to run a tool) and the first tools, `memory_search_facts` and `memory_search_notes` (read only), run from `steel tool call` with no model. Open: legacy names (POLICY.md section 6); per-person handling of memory, now that a tool can read facts (docs/MEMORY_QUALITY.md); whether the chat model may ever call a tool (needs a prompt-injection review first).
5. Voice and vision: define consent, visible recording indicators and retention before attaching sensors. Room observations build on the expiring note store.
5a. Phase 2c (voice, push-to-talk only; no wake word, no always-on listening): the design is written first in docs/VOICE.md; implementation follows it and is marked done here only with named tests.
6. Mobile/API: authentication and authorization must come before any remote command interface.

These are planned milestones with no promised dates. The security module will be limited to owned or explicitly authorized environments.
