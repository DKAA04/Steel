# Security and privacy boundaries

STEEL is an early local prototype. Run examples with invented notes. The current executable has no remote services, microphone/camera access, model calls or shell execution.

- Persistent storage requires explicit opt-in. Store its directory under your own operating-system account permissions.
- SQLite files are not encrypted. Do not store passwords, tokens, personal histories or client information.
- Queries are parameterized; searches interpret user text literally.
- Notes expire on access. No background deletion occurs while the program is closed.
- SQL deletion and SQLite secure-delete settings cannot guarantee removal from filesystem snapshots, SSD remapping, swap, backups or shell history.
- The CLI is intended for one trusted local user. It provides neither authentication nor multi-user isolation.
- Voice, vision and remote integrations require a separate privacy and security review before implementation.

Do not publish real data in issues. Report a reproducible problem using synthetic examples and the affected code path. No legal compliance, independent security audit or certification is claimed.
