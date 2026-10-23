# Provenance

STEEL imports OpenJarvis (Apache-2.0, https://github.com/open-jarvis/OpenJarvis) from the pinned install at commit `108d257188cbad85f2e50209d9a61669ea4dec33`. Imported code is not copied into this repository and is not modified.

Any file ported (copied and adapted) from OpenJarvis must start with a header giving its source path, the commit, the Apache-2.0 license and what changed. It must also get a row here.

| STEEL file | OpenJarvis source path | Commit | What changed |
| --- | --- | --- | --- |
| `steel/security/policy.py` | `src/openjarvis/security/capabilities.py` (`CapabilityPolicy._check_python`, `AgentPolicy`, `CapabilityGrant`, and the policy-file validation in `_load_file`) | `108d257188cbad85f2e50209d9a61669ea4dec33` | Pure Python only (no Rust). **Inverted from open-by-default to deny-by-default**: an unknown agent, an unmatched capability or any error is a deny, and the `_default` fallback agent is removed. Fixed capability vocabulary; exact matching instead of `fnmatch`; path grants matched on resolved path components; sensors never covered by a wildcard; every decision written to a names-only audit log; strict policy-file validation. Details: docs/POLICY.md |

`steel/security/executor.py` subclasses `openjarvis.tools._stubs.ToolExecutor` and contains no copied code. `steel/facts.py` subclasses `openjarvis.memory.store.LocalFactStore` and uses its private members. It contains no copied code.
