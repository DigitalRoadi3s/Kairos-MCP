# CalDAV REST Gateway

Exposes CalDAV calendar systems (iCloud, Google Calendar, Nextcloud) as a REST API for Claude to call natively. See `docs/caldav-rest-connector-prd.md` for the full spec.

## Status

Work is broken into a dependency-ordered task list, split by model tier (Opus/Sonnet/Haiku) based on task complexity — see `docs/caldav-gateway-subagent-plan.md`. Subagent definitions for Claude Code live in `.claude/agents/`.

Progress is tracked via commits — each task in the plan lands as its own commit (or PR) referencing its task number, e.g. `task 3: CalDAV client + iCal transform core`.

| Phase | Status |
|-------|--------|
| Phase 1: MVP | in progress (tasks 1, 3 done) |
| Phase 2: Write support | not started |
| Phase 3: Polish & Ops | not started |
| Phase 4: Advanced | backlog |

## Layout

```
src/               application code
docs/              PRD + task/subagent plan
.claude/agents/    Claude Code subagent definitions (opus/sonnet/haiku)
```

## Running locally

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080
```

## Docker

```bash
docker build -t caldav-rest-gateway:latest .
docker run -p 8080:8080 caldav-rest-gateway:latest
```
