# STEEL development context

STEEL is a layer on top of OpenJarvis, imported from `~/.openjarvis/.venv` and pinned to commit `108d257188cbad85f2e50209d9a61669ea4dec33` (`steel/pin.py`). Never edit the OpenJarvis source at `~/.openjarvis/src`. Import its shared core (registry, events, types); do not copy it. Any ported file needs a header and a row in docs/PROVENANCE.md.

Implemented (Phase 1): `steel chat` with persistent OpenJarvis memory, `SteelFactStore` forget-by-id, list/forget/wipe across all memory stores, and the expiring SQLite notes. No tools are registered. Phase 2 starts with the `SteelPolicy` permission policy, before any tool. See README.md for the capability table.

Use Python 3.11–3.13. Runtime depends on OpenJarvis only. Type-hint runtime code, format and lint with Ruff, type-check `steel` with mypy, and run `pytest` (it must never call the live model) before commits. Keep examples synthetic and never commit local databases, credentials or recordings.

Do not describe planned capabilities as implemented, claim GDPR compliance or add external actions without a reviewed consent and authorization design. Keep README, SECURITY.md and ARCHITECTURE.md synchronized with what the code does. Record AI-assisted implementation honestly.
