# Kairos-MCP

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
export CALDAV_SOURCES='[{"id":"icloud","name":"Personal","url":"https://caldav.icloud.com/","username":"you@icloud.com","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/caldav/v2/you@icloud.com/calendar/personal/"}]'
```

**What to actually change before this works:**

| Placeholder | Replace with | Shows up in |
|---|---|---|
| `you@icloud.com` | Your real iCloud email | `username`, and usually inside `calendar_path` too |
| `xxxx-xxxx-xxxx-xxxx` | An app-specific password (16 chars, NOT your Apple ID password — generate one at icloud.com/account/security) | `password` |
| `/caldav/v2/you@icloud.com/calendar/personal/` | The real calendar path — see below, don't guess it | `calendar_path` |
| `"id":"icloud"` | Any nickname you want | Whatever you put here must match in every curl example below (`/calendars/icloud/...`) |

#### Finding your calendar path

`https://caldav.icloud.com/` is the address of your whole iCloud account. Each calendar has its own path underneath that — often a random-looking code, not the calendar's display name. Look it up:

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

Copy the `Path` of the calendar you want into `calendar_path` — verbatim.

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

### Daily brief example

`icloud` is the `"id"` from your `CALDAV_SOURCES` config.

```bash
curl "http://localhost:8080/api/v1/calendars/icloud/today?timezone=America/New_York"
```

```json
{
  "calendar_id": "icloud",
  "date": "2026-08-14",
  "summary": {
    "total_events": 6,
    "total_calendar_time_minutes": 270,
    "top_attendees": [{"email": "alice@company.com", "name": "Alice", "count": 2}],
    "free_slots": [
      {"start": "2026-08-14T10:30:00-04:00", "end": "2026-08-14T12:30:00-04:00", "duration_minutes": 120}
    ]
  },
  "events": [ ... ]
}
```

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
