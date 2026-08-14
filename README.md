# CalDAV REST Gateway

Exposes CalDAV calendar systems (iCloud, Google Calendar, Nextcloud) as a REST API. Read events, get a daily brief with free-slot analysis, and create/update/delete events — including recurring ones — all over plain HTTP/JSON instead of raw CalDAV/iCalendar.

## Features

- Read events in a date range, or a computed daily brief (busy blocks, free slots, attendee summary) for any calendar
- Create, update, and delete events, including recurring events (RRULE)
- Multiple calendar sources at once — mix iCloud, Nextcloud, and Google Calendar in one config
- Circuit breaker + retry/backoff around every CalDAV call, so one flaky provider doesn't take down the others
- Structured JSON logs, Prometheus metrics, health check per source

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

`https://caldav.icloud.com/` is the address of your whole iCloud account. If you have more than one calendar (Personal, Work, Family...), each one has its own separate path underneath that — and it's often a random-looking code, not the calendar's actual name, so you can't guess it. Look it up:

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

### Nextcloud

Same config shape as iCloud, no code changes needed.

```bash
export CALDAV_SOURCES='[{"id":"nextcloud","name":"Nextcloud Personal","url":"https://your-nextcloud-domain/remote.php/dav/","username":"your-nextcloud-username","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"/remote.php/dav/calendars/your-nextcloud-username/personal/"}]'
```

| Placeholder | Replace with |
|---|---|
| `your-nextcloud-domain` | Your Nextcloud server's domain |
| `your-nextcloud-username` | Your Nextcloud login username (not necessarily your email) |
| `xxxx-xxxx-xxxx-xxxx` | An app password — Nextcloud web UI → your avatar → Settings → Security → "Create new app password" |
| `.../calendars/your-nextcloud-username/personal/` | The calendar's slug, not its display name — the Calendar app's Settings panel shows "Copy primary CalDAV address" for the base, and each calendar's exact path in its own settings |

### Google Calendar

Google disabled username/password CalDAV access on March 14, 2025 — this requires OAuth 2.0. One-time setup to get a refresh token, then it renews itself automatically:

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project (or use an existing one), enable the **Google Calendar API**, and create an **OAuth 2.0 Client ID** of type **Desktop app** — this gives you a `client_id` and `client_secret`.
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
   This opens a browser for you to sign in and grant access, then prints a refresh token.
3. Find your calendar ID: Google Calendar web UI → calendar's settings (⋮ menu) → "Integrate calendar" → **Calendar ID**. For your primary calendar this is just your email address.

```bash
export CALDAV_SOURCES='[{"id":"google","auth_type":"oauth2","url":"https://apidata.googleusercontent.com/caldav/v2/your-calendar-id/events","client_id":"xxx.apps.googleusercontent.com","client_secret":"xxxx","refresh_token":"xxxx","calendar_path":"https://apidata.googleusercontent.com/caldav/v2/your-calendar-id/events"}]'
```

| Placeholder | Replace with |
|---|---|
| `your-calendar-id` | The Calendar ID from step 3 (your email, for the primary calendar) |
| `client_id` / `client_secret` | From step 1 |
| `refresh_token` | From step 2 |

You can mix any combination of iCloud, Nextcloud, and Google sources in the same `CALDAV_SOURCES` array — each just needs a unique `id`.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/calendars` | List configured calendars |
| GET | `/api/v1/calendars/{id}/events` | List events in a date range |
| GET | `/api/v1/calendars/{id}/today` | Daily brief: today's events, free slots, attendee summary |
| POST | `/api/v1/calendars/{id}/events` | Create an event (supports `recurrence_rule`) |
| PUT | `/api/v1/calendars/{id}/events/{uid}` | Update an event (locked within 15 min of start; recurrence itself is immutable via PUT) |
| DELETE | `/api/v1/calendars/{id}/events/{uid}` | Delete an event (same 15-minute lock) |
| GET | `/health` | Per-source connection + circuit-breaker status |
| GET | `/metrics` | Prometheus metrics |

### Daily brief example

`icloud` below is the `"id"` you picked in `CALDAV_SOURCES` — if you named yours something else, change it here too.

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

Or with docker-compose (see `docker-compose.yml` for a full example):

```bash
docker compose up
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
```
