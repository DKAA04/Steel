# Provenance

STEEL imports OpenJarvis (Apache-2.0, https://github.com/open-jarvis/OpenJarvis) from the pinned install at commit `108d257188cbad85f2e50209d9a61669ea4dec33`. Imported code is not copied into this repository and is not modified.

Any file ported (copied and adapted) from OpenJarvis must start with a header giving its source path, the commit, the Apache-2.0 license and what changed. It must also get a row here.

| STEEL file | OpenJarvis source path | Commit | What changed |
| --- | --- | --- | --- |
| *(none: no files are ported in Phase 1)* | | | |

`steel/facts.py` subclasses `openjarvis.memory.store.LocalFactStore` and uses its private members. It contains no copied code.
