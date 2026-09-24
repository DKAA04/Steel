# STEEL

**A local assistant project, starting with explicit, temporary memory.**

STEEL explores a room-scale assistant combining voice, vision and local tools. The current release is a small, runnable Python foundation: a command interface and SQLite note store. Voice, camera perception, LLM orchestration, the mobile bridge and security tools remain planned.

## Try it in 30 seconds

Python 3.11+; no runtime packages, accounts, model downloads or hardware required.

```bash
git clone https://github.com/DKAA04/Steel.git
cd Steel
python -m core demo
python -m core status
python -m unittest discover -s tests -v
```

The demo stores an invented sensor reminder in RAM, searches it and deletes it. It does not record audio/video, make network requests or execute shell commands.

## Explicit local memory

For a persistent note, choose a private local directory and opt in:

```bash
mkdir -m 700 data
python -m core --db data/notes.sqlite3 --allow-persistence remember "Synthetic reminder: inspect the lab sensor" --ttl-hours 24
python -m core --db data/notes.sqlite3 --allow-persistence recall sensor
python -m core --db data/notes.sqlite3 --allow-persistence forget NOTE_ID --confirm
```

Replace `NOTE_ID` with the identifier returned by `remember`. On Windows, create the `data` directory with access restricted to your own account. Note text supplied on a command line can enter shell history; use invented text for demonstrations and never store credentials here.

Notes expire after 24 hours by default (maximum seven days); expired entries are removed when memory is queried. Disk storage is **unencrypted**. Deleting an entry is not a guarantee of forensic erasure from storage, backups or OS caches. See [security and privacy boundaries](SECURITY.md).

## Implementation status

| Component | Current state |
| --- | --- |
| Command interface | Implemented: demo, status, remember, recall and confirmed deletion |
| Memory | Implemented: in-memory default, opt-in SQLite storage, parameterized queries, retention checks |
| Voice / vision | Planned; no microphones, cameras or identity recognition are connected |
| Agent / local model | Planned; no autonomous action execution |
| API / Flutter bridge | Planned; no listening service |
| Cybersecurity tools | Planned; no scanner or offensive module is shipped |

[Architecture](ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md) · [Tests](tests/test_memory.py)

## Development and provenance

The original repository was a directory scaffold and architecture outline. This runnable foundation, tests and documentation were added with Codex assistance during a September 2026 portfolio review. They should not be presented as a previously completed room-scale AI system or as evidence of deployed client work.

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check core memory tests
python -m mypy core memory
python -m pytest -q
```

No GDPR compliance certification or production-readiness claim is made. The broader product requires consent design, threat modelling, access control and hardware/integration evaluation before deployment.
