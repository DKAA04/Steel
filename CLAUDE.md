# STEEL — Claude Code Context

## Project
STEEL is a room-scale AI assistant. Local-first, GDPR-compliant.
Modules: core (LangGraph orchestration), vision (YOLOv8), voice (Whisper + wake word),
memory (persistent store), security (offensive cybersec module), api (FastAPI for
phone bridge), mobile (Flutter app), deploy (Docker + systemd).

Goal: solo build, 17-week plan, exit-grade quality.

## Stack
- Python 3.13 (orchestration, ML) — note: some ML libs may need 3.11/3.12 fallback
- LangGraph for agent workflow
- Ollama for local LLM inference
- YOLOv8 (ultralytics) for vision
- OpenAI Whisper for voice
- FastAPI for service layer
- Flutter for mobile bridge
- Docker for deployment

## Conventions
- Type-hint everything. mypy must pass.
- Ruff for lint/format. Run before committing.
- Tests in `tests/` mirroring source structure. pytest.
- No secrets in code. Use `.env` (gitignored) and `.env.example` for shape.
- GDPR: never log raw PII or audio/video frames. Mask in logs.

## gstack
gstack is installed globally at `~/.claude/skills/gstack/`. Use its slash commands directly.

Skills to USE on this project:
/office-hours, /autoplan, /plan-eng-review, /plan-ceo-review,
/review, /qa, /qa-only, /investigate, /retro,
/cso, /setup-gbrain, /sync-gbrain,
/careful, /freeze, /guard, /unfreeze,
/document-release, /codex, /gstack-upgrade, /learn,
/context-save, /context-restore.

Skills to NOT use (web/SaaS-shaped, wrong fit for STEEL):
/design-html, /design-consultation, /design-shotgun, /design-review,
/plan-design-review, /land-and-deploy, /setup-deploy, /canary,
/browse, /open-gstack-browser, /setup-browser-cookies, /pair-agent,
/plan-devex-review, /devex-review, /scrape, /landing-report, /make-pdf.

If a gstack skill misbehaves, run `cd ~/.claude/skills/gstack && ./setup`.

## Workflow
Every feature follows this loop:
1. /office-hours — scope and forcing questions
2. /plan-eng-review — lock architecture
3. implement (manual or Claude-driven)
4. /review — paranoid code review
5. /qa — find bugs, add regression tests
6. /cso — when touching security or GDPR-relevant code
7. /document-release — keep ARCHITECTURE.md and this file current
8. commit + PR (no auto-deploy; this is local-first)
