# CalDAV REST Gateway

Exposes CalDAV calendar systems (iCloud, Google Calendar, Nextcloud) as a REST API for Claude to call natively. See `docs/caldav-rest-connector-prd.md` for the full spec.

## Status

Work is broken into a dependency-ordered task list, split by model tier (Opus/Sonnet/Haiku) based on task complexity — see `docs/caldav-gateway-subagent-plan.md`. Subagent definitions for Claude Code live in `.claude/agents/`.

Progress is tracked via commits — each task in the plan lands as its own commit (or PR) referencing its task number, e.g. `task 3: CalDAV client + iCal transform core`.

| Phase | Status |
|-------|--------|
| Phase 1: MVP | done — tasks 1, 2, 3, 5, 6, 9 (task 7 covered by task 6, task 8 = this file) |
| Phase 2: Write support | not started |
| Phase 3: Polish & Ops | not started |
| Phase 4: Advanced | backlog |

## Configuration

Set `CALDAV_SOURCES` to a JSON array (see PRD FR-1 / Appendix A Step 4 for the iCloud app-specific-password walkthrough):

```bash
export CALDAV_SOURCES='[{"id":"icloud","name":"Personal","url":"https://caldav.icloud.com/","username":"you@icloud.com","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/caldav/v2/you@icloud.com/calendar/personal/"}]'
```

## Daily brief example

```bash
curl "http://localhost:8080/api/v1/calendars/icloud/today?timezone=America/New_York"
```

```json
{
  "calendar_id": "icloud",
  "date": "2026-08-12",
  "summary": {
    "total_events": 6,
    "total_calendar_time_minutes": 270,
    "top_attendees": [{"email": "alice@company.com", "name": "Alice", "count": 2}],
    "free_slots": [
      {"start": "2026-08-12T10:30:00-04:00", "end": "2026-08-12T12:30:00-04:00", "duration_minutes": 120}
    ]
  },
  "events": [ ... ]
}
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

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
