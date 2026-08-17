# Kairos-MCP

**Current version: 1.0.1** — see [CHANGELOG.md](CHANGELOG.md)

A CalDAV REST gateway and MCP server for **multi-agent homelab setups** — where more than one model (Claude, Hermes, a local LLM via OpenClaw) needs to read and write the same calendars through a shared, observable backend.

## What this is

Kairos-MCP sits between your LLMs and your CalDAV providers. It exposes iCloud and Nextcloud calendars as a REST API (with circuit-breaker, rate-limit handling, Prometheus metrics, and structured logs), then wraps that API as an MCP server and an OpenAI-compatible tool spec so any agent can call it.

The REST layer is the point. A single running gateway means:
- Rate limit budget is shared across all consumers (critical — iCloud is aggressive)
- One circuit breaker protects every model from a flaky provider
- Credentials live in one place, not in every agent's config
- You can curl it, script against it, and monitor it independently of whatever agent is calling it

## What this isn't

**The simplest way to get Claude talking to your calendar.** If that's what you want:

- **iCloud + Claude Desktop**: [`mcp-calendars`](https://github.com/lucasheight/mcp-calendars) is a one-command install that handles iCloud, Nextcloud, Google, and more with a setup wizard
- **Google Calendar + Claude**: Google ships an official MCP server at `https://calendarmcp.googleapis.com/mcp/v1` (Developer Preview) — 9 tools including free-time suggestion and event search, OAuth handled for you
- **iCloud-only**: [`icloud-calendar-mcp`](https://www.npmjs.com/package/@icloud-calendar-mcp/server) and [`mcp-icloud-calendar`](https://github.com/roygabriel/mcp-icloud-calendar) are both good single-purpose options

Start with one of those. Come back here when you're running multiple models and need them sharing a backend.

## Features

- iCloud and Nextcloud via CalDAV (basic auth / app-specific passwords)
- Google Calendar via CalDAV (OAuth2 refresh-token — see note below)
- Circuit breaker + exponential backoff per source
- 5-minute free-slot cache with write-through invalidation
- Structured JSON logs, Prometheus metrics (`/metrics`), per-source health (`/health`)
- MCP server for Claude Desktop, Claude Code, and OpenClaw (see `skill/`)
- OpenAI-compatible tool spec for Hermes, LiteLLM, Ollama (see `skill/tools.json`)

## Configuration

Set `CALDAV_SOURCES` to a JSON array — one object per calendar you want exposed.

### iCloud

```bash
export CALDAV_SOURCES='[{"id":"icloud","name":"Personal","url":"https://caldav.icloud.com/","username":"you@icloud.com","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"https://p178-caldav.icloud.com/ACCOUNT_ID/calendars/CALENDAR_UUID/"}]'
```

**What to actually change before this works:**

| Placeholder | Replace with | Shows up in |
|---|---|---|
| `you@icloud.com` | Your real iCloud email | `username` |
| `xxxx-xxxx-xxxx-xxxx` | An app-specific password (16 chars, NOT your Apple ID password — generate one at icloud.com/account/security) | `password` |
| `https://p178-caldav.icloud.com/ACCOUNT_ID/calendars/CALENDAR_UUID/` | The full calendar URL from the discovery script below — don't guess it, don't use a relative path | `calendar_path` |
| `"id":"icloud"` | Any nickname you want | Whatever you put here must match in every curl example below (`/calendars/icloud/...`) |

#### Finding your calendar URL

iCloud routes each account to a specific shard server (`p178-caldav.icloud.com`, `p30-caldav.icloud.com`, etc.) and identifies calendars by UUID, not display name. The URL you need looks like `https://p178-caldav.icloud.com/58373301/calendars/A7A8B621-7523-48DF-B3A2-0B6C0481FFC5/` — you cannot construct it manually. Run this to find yours:

```bash
pip install caldav
python3 - <<'EOF'
from caldav import DAVClient
email = input("Enter iCloud email: ")
password = input("Enter app-specific password: ")
client = DAVClient(url="https://caldav.icloud.com/", username=email, password=password)
for cal in client.principal().calendars():
    print(f"Display Name: {cal.name}")
    print(f"Full URL:     {cal.url}")
    print("---")
EOF
```

Copy the **Full URL** of the calendar you want into `calendar_path` — the entire `https://...` string, verbatim. The `url` field in the config stays as `https://caldav.icloud.com/`; only `calendar_path` gets the shard-specific URL.

### Nextcloud

Same config shape as iCloud, no code changes needed.

```bash
export CALDAV_SOURCES='[{"id":"nextcloud","name":"Nextcloud Personal","url":"https://your-nextcloud-domain/remote.php/dav/","username":"your-nextcloud-username","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/remote.php/dav/calendars/your-nextcloud-username/personal/"}]'
```

| Placeholder | Replace with |
|---|---|
| `your-nextcloud-domain` | Your Nextcloud server's domain |
| `your-nextcloud-username` | Your Nextcloud login username |
| `xxxx-xxxx-xxxx-xxxx` | An app password — Nextcloud web UI → your avatar → Settings → Security → "Create new app password" |
| `.../calendars/your-nextcloud-username/personal/` | The calendar's slug — the Calendar app Settings panel shows each calendar's exact path |

Multiple sources (iCloud and Nextcloud together) go in the same array — each needs a unique `id`.

### Google Calendar

> **Before reaching for this path**: if you only need Google Calendar with Claude, Google's [official Calendar MCP server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server) (`calendarmcp.googleapis.com`) is simpler and has more tools (free-time suggestion, event search, RSVP). It's still in Developer Preview, but it handles auth for you.
>
> Use the CalDAV path here when you specifically need Google routed through this gateway — so it shares rate-limit budget and circuit-breaker state with your other sources in a multi-agent setup.

Google disabled username/password CalDAV access in March 2025. The CalDAV path requires a refresh token from a one-time OAuth consent flow:

1. In [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Calendar API** and create an **OAuth 2.0 Client ID** (Desktop app type) — gives you a `client_id` and `client_secret`.
2. Run this once to get a refresh token:
   ```bash
   pip install google-auth-oauthlib
   python3 - <<'EOF'
   from google_auth_oauthlib.flow import InstalledAppFlow
   client_id = input("client_id: ")
   client_secret = input("client_secret: ")
   flow = InstalledAppFlow.from_client_config(
       {"installed": {"client_id": client_id, "client_secret": client_secret,
                      "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                      "token_uri": "https://oauth2.googleapis.com/token"}},
       scopes=["https://www.googleapis.com/auth/calendar"],
   )
   creds = flow.run_local_server(port=0)
   print("refresh_token:", creds.refresh_token)
   EOF
   ```
3. Find your calendar ID: Google Calendar web UI → calendar settings → "Integrate calendar" → **Calendar ID** (your email for the primary calendar).

```bash
export CALDAV_SOURCES='[{"id":"google","auth_type":"oauth2","url":"https://apidata.googleusercontent.com/caldav/v2/your-calendar-id/events","client_id":"xxx.apps.googleusercontent.com","client_secret":"xxxx","refresh_token":"xxxx","calendar_path":"https://apidata.googleusercontent.com/caldav/v2/your-calendar-id/events"}]'
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/calendars` | List configured calendars |
| GET | `/api/v1/calendars/{id}/events` | List events in a date range |
| GET | `/api/v1/calendars/{id}/today` | Daily brief: today's events, free slots, attendee summary |
| POST | `/api/v1/calendars/{id}/events` | Create an event (supports `recurrence_rule`) |
| PUT | `/api/v1/calendars/{id}/events/{uid}` | Update an event (locked within 15 min of start) |
| DELETE | `/api/v1/calendars/{id}/events/{uid}` | Delete an event (same 15-minute lock) |
| GET | `/health` | Per-source connection + circuit-breaker status |
| GET | `/metrics` | Prometheus metrics |

### List calendars

```bash
curl http://localhost:8080/api/v1/calendars
```

### List events

```bash
# Next 7 days
curl "http://localhost:8080/api/v1/calendars/icloud/events?date_min=2026-08-14T00:00:00Z&date_max=2026-08-21T00:00:00Z"

# With a result cap
curl "http://localhost:8080/api/v1/calendars/icloud/events?date_min=2026-08-14T00:00:00Z&date_max=2026-09-14T00:00:00Z&limit=25"
```

Parameters: `date_min` and `date_max` (ISO 8601, default: today → +30 days), `limit` (1–1000, default 100).

### Daily brief

```bash
curl "http://localhost:8080/api/v1/calendars/icloud/today?timezone=America/New_York"
```

Returns today's events, free-slot analysis (gaps > 15 min), total calendar time, and attendee summary. `timezone` defaults to UTC.

### Create an event

```bash
curl -X POST http://localhost:8080/api/v1/calendars/icloud/events \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Team standup",
    "start_time": "2026-08-20T14:00:00-04:00",
    "end_time":   "2026-08-20T14:30:00-04:00",
    "location":   "Conf room B",
    "attendees":  [{"email": "alice@example.com", "name": "Alice"}],
    "status":     "confirmed"
  }'
```

Create a recurring event by adding `recurrence_rule` (RFC 5545 RRULE):

```bash
curl -X POST http://localhost:8080/api/v1/calendars/icloud/events \
  -H "Content-Type: application/json" \
  -d '{
    "title":            "Weekly standup",
    "start_time":       "2026-08-17T09:00:00-04:00",
    "end_time":         "2026-08-17T09:30:00-04:00",
    "recurrence_rule":  "FREQ=WEEKLY;BYDAY=MO,WE,FR"
  }'
```

Returns the created event including its `uid` — save that if you need to update or delete it later. Events starting within 15 minutes cannot be modified after creation.

### Update an event

PATCH semantics — only fields you include are changed; everything else stays as-is.

```bash
# Rename an event
curl -X PUT http://localhost:8080/api/v1/calendars/icloud/events/EVENT-UID-HERE \
  -H "Content-Type: application/json" \
  -d '{"title": "Team standup (rescheduled)"}'

# Reschedule
curl -X PUT http://localhost:8080/api/v1/calendars/icloud/events/EVENT-UID-HERE \
  -H "Content-Type: application/json" \
  -d '{
    "start_time": "2026-08-20T15:00:00-04:00",
    "end_time":   "2026-08-20T15:30:00-04:00"
  }'

# Change status to tentative
curl -X PUT http://localhost:8080/api/v1/calendars/icloud/events/EVENT-UID-HERE \
  -H "Content-Type: application/json" \
  -d '{"status": "tentative"}'
```

The `recurrence_rule` of a recurring event cannot be changed via update — delete and recreate to change the recurrence pattern.

### Delete an event

```bash
curl -X DELETE http://localhost:8080/api/v1/calendars/icloud/events/EVENT-UID-HERE
```

Events starting within 15 minutes are locked and cannot be deleted.

### Health check

```bash
curl http://localhost:8080/health
```

Shows connection status and circuit-breaker state per calendar source. Returns 200 when all sources are healthy, 503 if none connected.

### Prometheus metrics

```bash
curl http://localhost:8080/metrics
```

Exposes `caldav_request_duration_seconds`, `caldav_calendar_sync_errors_total`, `api_request_duration_seconds`, `api_errors_total`, and `caldav_rate_limit_remaining`.

## MCP and tool definitions

See [`skill/`](skill/) — MCP server for Claude Desktop / Claude Code / OpenClaw, plus OpenAI-compatible tool definitions for Hermes, LiteLLM, and Ollama.

## Running locally

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080
```

## Docker

```bash
docker build -t kairos-mcp:latest .
docker compose up   # see docker-compose.yml for full example
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Layout

```
src/     application code
tests/   test suite
skill/   MCP server + OpenAI tool definitions
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
