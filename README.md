# CalDAV REST Gateway

Exposes CalDAV calendar systems (iCloud, Google Calendar, Nextcloud) as a REST API for Claude to call natively. See `docs/caldav-rest-connector-prd.md` for the full spec.

## Status

Work is broken into a dependency-ordered task list, split by model tier (Opus/Sonnet/Haiku) based on task complexity — see `docs/caldav-gateway-subagent-plan.md`. Subagent definitions for Claude Code live in `.claude/agents/`.

Progress is tracked via commits — each task in the plan lands as its own commit (or PR) referencing its task number, e.g. `task 3: CalDAV client + iCal transform core`.

| Phase | Status |
|-------|--------|
| Phase 1: MVP | done — tasks 1, 2, 3, 5, 6, 9 (task 7 covered by task 6, task 8 = this file) |
| Phase 2: Write support | done — tasks 10, 11, 12, 13, 14, 15 |
| Phase 3: Polish & Ops | done — tasks 16-23 |
| Phase 4: Advanced | backlog |

## Configuration

Set `CALDAV_SOURCES` to a JSON array (see PRD FR-1 / Appendix A Step 4 for the iCloud app-specific-password walkthrough):

```bash
export CALDAV_SOURCES='[{"id":"icloud","name":"Personal","url":"https://caldav.icloud.com/","username":"you@icloud.com","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/caldav/v2/you@icloud.com/calendar/personal/"}]'
```

**What to actually change before this works:**

| Placeholder | Replace with | Shows up in |
|---|---|---|
| `you@icloud.com` | Your real iCloud email | `username`, and usually inside `calendar_path` too |
| `xxxx-xxxx-xxxx-xxxx` | Your app-specific password (16 chars, NOT your Apple ID password — generate one at icloud.com/account/security) | `password` |
| `/caldav/v2/you@icloud.com/calendar/personal/` | The real calendar path — see below, don't guess it | `calendar_path` |
| `"id":"icloud"` | Any nickname you want | Whatever you put here must match in every curl example below (`/calendars/icloud/...`) |

### Finding your calendar path

`https://caldav.icloud.com/` is the address of your whole iCloud account. If you have more than one calendar (Personal, Work, Family...), each one has its own separate path underneath that — and it's often a random-looking code, not the calendar's actual name, so you can't guess it. You have to look it up:

```bash
pip install caldav
python3 - <<'EOF'
from caldav import DAVClient
email = input("Enter iCloud email: ")
password = input("Enter app-specific password: ")
client = DAVClient(url="https://caldav.icloud.com/", username=email, password=password)
for cal in client.principal().calendars():
    print(f"Display Name: {cal.get_properties()['{DAV:}displayname']}")
    print(f"Path:         {cal.url}")
    print("---")
EOF
```

This logs in and prints every calendar you have, with its exact path. Copy the `Path` of the one you want into `calendar_path` above — verbatim, not retyped.

### Connecting Nextcloud

Nextcloud works the same way as iCloud — same config shape, no code changes needed.

```bash
export CALDAV_SOURCES='[{"id":"nextcloud","name":"Nextcloud Personal","url":"https://your-nextcloud-domain/remote.php/dav/","username":"your-nextcloud-username","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/remote.php/dav/calendars/your-nextcloud-username/personal/"}]'
```

| Placeholder | Replace with |
|---|---|
| `your-nextcloud-domain` | Your Nextcloud server's domain |
| `your-nextcloud-username` | Your Nextcloud login username (not necessarily your email) |
| `xxxx-xxxx-xxxx-xxxx` | An app password — Nextcloud web UI → your avatar → Settings → Security → "Create new app password" |
| `.../calendars/your-nextcloud-username/personal/` | The calendar's slug, not its display name — check the Calendar app's Settings panel, "Copy primary CalDAV address" shows the base, then the calendar list shows each one's exact path |

If you have multiple sources (iCloud and Nextcloud both), just put both objects in the same `CALDAV_SOURCES` array — each needs a unique `id`.

### Connecting Google Calendar — not supported yet

Google turned off username/password access to Google Calendar's CalDAV for everyone on March 14, 2025. It now requires OAuth 2.0 — a browser-based login and token exchange, not a password you can paste into a config file. This gateway's client only supports username/password auth today, so **no `CALDAV_SOURCES` entry will make Google Calendar work right now**, regardless of what you put in it. Adding Google support means building an OAuth flow into the client first (tracked as Phase 4 backlog, task 24) — it's not a configuration problem to work around.

## Daily brief example

`icloud` below is the `"id"` you picked in `CALDAV_SOURCES` above — if you named yours something else, change it here too.

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
