# STEEL development context

The implemented baseline is an offline Python CLI and explicit SQLite memory store. See README.md for the current capability table; voice, vision, LLM orchestration, mobile and cybersecurity integrations remain planned.

Use Python 3.11+. Runtime code currently uses only the standard library. Type-hint runtime code, format and lint with Ruff, type-check core and memory with mypy, and run tests before commits. Keep examples synthetic and never commit local databases, credentials or recordings.

Do not describe planned capabilities as implemented, claim GDPR compliance or add external actions without a reviewed consent and authorization design. Keep the architecture and security boundaries synchronized with changes. Record AI-assisted implementation honestly.
