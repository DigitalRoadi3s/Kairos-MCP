# Kairos-MCP
<img src="assets/kairos-logo.png" alt="Kairos-MCP logo" width="160" />

**Current version: 2.0.0** (see [CHANGELOG.md](CHANGELOG.md); 2.0 has breaking changes)

A CalDAV gateway and MCP server for **multi-agent homelab setups**, where more than one model (Claude, Hermes, a local LLM via OpenClaw) needs to read and write the same calendars through a shared, observable backend.

## What this is

Kairos-MCP sits between your LLMs and your CalDAV providers. One process serves iCloud, Nextcloud and Google calendars two ways:

- **MCP over streamable HTTP** at `/mcp`, for Claude (claude.ai, Claude Desktop, Claude Code) and any other MCP client
- **A REST API** at `/api/v1`, for scripts, curl, and OpenAI-style function calling (Hermes, LiteLLM, Ollama)

Both sit on the same core, so every consumer shares:
- One rate-limit budget per provider (critical: iCloud is aggressive)
- One circuit breaker per source, so a flaky provider fails fast for everyone
- One place for credentials, instead of every agent's config
- One set of metrics, logs and health checks

## What this isn't

**The simplest way to get Claude talking to your calendar.** If that's what you want:

- **iCloud + Claude Desktop**: [`mcp-calendars`](https://github.com/lucasheight/mcp-calendars) is a one-command install that handles iCloud, Nextcloud, Google, and more with a setup wizard
- **Google Calendar + Claude**: Google ships an official MCP server at `https://calendarmcp.googleapis.com/mcp/v1` (Developer Preview), with OAuth handled for you
- **iCloud-only**: [`icloud-calendar-mcp`](https://www.npmjs.com/package/@icloud-calendar-mcp/server) and [`mcp-icloud-calendar`](https://github.com/roygabriel/mcp-icloud-calendar) are both good single-purpose options

Start with one of those. Come back here when you're running multiple models and need them sharing a backend.

## Features

- iCloud and Nextcloud via CalDAV (app-specific passwords), Google via CalDAV with an OAuth2 refresh token
- Edits change only the fields you send: alarms, RSVPs, categories and vendor properties survive
- Optimistic concurrency: an edit that races another client gets a 409, not a silent overwrite
- Events are stored in the calendar's own time zone (TZID plus VTIMEZONE), so they stay correct across DST
- Read-only calendars (`"writable": false`) are enforced
- Circuit breaker with backoff per source; writes are never retried, so a timeout can't create duplicates
- Bearer-token auth on everything; OAuth for `/mcp` through WorkOS AuthKit or your own identity provider
- Structured JSON logs, Prometheus metrics (`/metrics`), per-source health (`/health`)

## Quick start

```bash
docker build -t kairos-mcp .
docker run -p 8080:8080 \
  -e KAIROS_API_TOKENS="$(openssl rand -hex 32)" \
  -e CALDAV_SOURCES='[{"id":"icloud","name":"Personal","url":"https://caldav.icloud.com/","username":"you@icloud.com","password":"xxxx-xxxx-xxxx-xxxx","calendar_path":"https://p178-caldav.icloud.com/ACCOUNT_ID/calendars/CALENDAR_UUID/","timezone":"America/New_York"}]' \
  kairos-mcp
```

Or without Docker (Python 3.12+):

```bash
pip install .
kairos serve
```

See [`docker-compose.yml`](docker-compose.yml) for a fuller example with secrets.

## Calendar sources

Set `CALDAV_SOURCES` to a JSON array, one object per calendar. You can also put the same list in a YAML file (`sources:` key) at `/config/sources.yaml` or wherever `KAIROS_SOURCES_FILE` points, or point `CALDAV_SOURCES_FILE` at a JSON file (handy for Docker secrets).

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Your nickname; it's the `{calendar_id}` in every API path |
| `url` | yes | The provider's CalDAV root |
| `username`, `password` | basic auth | App-specific password for iCloud and Nextcloud |
| `auth_type` | no | `basic` (default) or `oauth2` (Google) |
| `client_id`, `client_secret`, `refresh_token` | oauth2 | See Google below |
| `calendar_path` | recommended | Full URL of the calendar; without it the first calendar found is used |
| `name` | no | Display name; defaults to `id` |
| `timezone` | no | IANA zone; new events are stored in it and floating times are read in it. Defaults to `DEFAULT_TIMEZONE`, then UTC |
| `writable` | no | `false` makes the calendar read-only through the gateway. Default `true` |
| `timeout` | no | Seconds per CalDAV request. Default 30 |

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

## Authentication

Every endpoint except `/health` and `/metrics` needs a bearer token.

**API tokens** work for REST and for `/mcp`. Generate one or more and pass them comma-separated:

```bash
export KAIROS_API_TOKENS="$(openssl rand -hex 32)"
```

Tokens must be at least 32 characters. `KAIROS_API_TOKENS_FILE` reads them from a file instead. With no tokens and no OIDC issuer configured, every request is refused; set `KAIROS_INSECURE_NO_AUTH=true` only on a trusted network.

**OAuth for `/mcp`** is for clients that can't send a static header, like claude.ai custom connectors. Kairos doesn't run its own login; it checks tokens from a login service you choose and publishes the discovery documents clients need to find it. API tokens keep working alongside OAuth.

### OAuth with WorkOS AuthKit

[AuthKit](https://workos.com/docs/authkit/mcp) is a hosted login service with a free tier and built-in support for MCP clients registering themselves.

In the WorkOS dashboard:

1. Create an account and an environment (start with Staging, use Production once it works). Note your **AuthKit domain**, like `example-123.authkit.app`.
2. Under **Connect → Configuration**, turn on **Client ID Metadata Document** and **Dynamic Client Registration**, so claude.ai can register itself.
3. On the same page, add a **Resource Indicator** of `https://cal.example.com/mcp` (your public URL plus `/mcp`) and set it as the default.
4. Sign in once through your AuthKit page (or add yourself under **Users**), then copy your user ID (`user_01...`) from **Users**.
5. If your AuthKit page lets anyone sign up, consider turning sign-up off. Kairos only lets in the user IDs you list either way.

Then on the gateway:

```bash
export KAIROS_PUBLIC_URL=https://cal.example.com
export KAIROS_WORKOS_AUTHKIT_DOMAIN=example-123.authkit.app
export KAIROS_OIDC_ALLOWED_SUBJECTS=user_01...           # required with AuthKit
```

Kairos checks each token's signature, issuer (`https://example-123.authkit.app`), audience (`https://cal.example.com/mcp`), expiry and user ID, and rejects WorkOS agent tokens. AuthKit access tokens last five minutes; Claude refreshes them on its own.

### OAuth with another identity provider

Any provider that issues **JWT** access tokens works (Authentik, Keycloak, Zitadel and similar):

```bash
export KAIROS_PUBLIC_URL=https://cal.example.com
export KAIROS_OIDC_ISSUER=https://auth.example.com/application/o/kairos/
export KAIROS_OIDC_REQUIRED_SCOPES=calendar              # optional
export KAIROS_OIDC_ALLOWED_SUBJECTS=your-user-id         # optional, recommended
```

The token's `aud` must be `https://cal.example.com/mcp`, or set `KAIROS_OIDC_AUDIENCE` to what your provider sends. Kairos finds the signing keys through the issuer's discovery document; set `KAIROS_OIDC_JWKS_URI` if discovery isn't available.

## Connecting clients

**Claude Code**

```bash
claude mcp add --transport http kairos https://cal.example.com/mcp \
  --header "Authorization: Bearer $KAIROS_TOKEN"
```

**claude.ai and Claude Desktop custom connectors**: add `https://cal.example.com/mcp` as a custom connector, then connect and sign in. This needs one of the OAuth setups above and a public https URL. With AuthKit, leave the connector's client ID and secret empty; Claude registers itself. With another provider that can't do that, create a client for Claude there and enter its client ID and secret in the connector's advanced settings.

**Local stdio** (Claude Desktop config, OpenClaw): `kairos mcp-stdio` runs the same tools over stdio with the same environment variables. It talks to CalDAV directly, so it doesn't share rate limits or circuit-breaker state with a running gateway; prefer `/mcp` when several agents are involved.

```json
{
  "mcpServers": {
    "kairos": {
      "command": "/path/to/venv/bin/kairos",
      "args": ["mcp-stdio"],
      "env": { "CALDAV_SOURCES_FILE": "/path/to/sources.json" }
    }
  }
}
```

**Hermes, LiteLLM, Ollama**: use [`skill/tools.json`](skill/tools.json) against the REST API. See [`skill/README.md`](skill/README.md).

### MCP tools

| Tool | What it does |
|---|---|
| `list_calendars` | Calendar ids, names, time zones, writable flag |
| `list_events` | Events in a range, one entry per occurrence; optional `timezone` for local times |
| `get_event` | One event by uid |
| `daily_brief` | Today's events, free slots, busy time, top attendees |
| `create_event` | New event, optionally recurring, with attendees |
| `update_event` | Change only the fields you pass |
| `delete_event` | Delete an event (whole series for recurring) |
| `gateway_health` | Per-source connection and circuit-breaker state |

Read tools are annotated read-only and the delete is annotated destructive, so clients can ask before running it.

## REST API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/calendars` | List configured calendars |
| GET | `/api/v1/calendars/{id}/events` | List events in a date range |
| GET | `/api/v1/calendars/{id}/events/{uid}` | Get one event |
| GET | `/api/v1/calendars/{id}/today` | Daily brief |
| POST | `/api/v1/calendars/{id}/events` | Create an event |
| PATCH or PUT | `/api/v1/calendars/{id}/events/{uid}` | Change an event (only the fields you send) |
| DELETE | `/api/v1/calendars/{id}/events/{uid}` | Delete an event |
| GET | `/health` | Per-source status; 503 when nothing is connected |
| GET | `/metrics` | Prometheus metrics |

All examples assume `export AUTH="Authorization: Bearer $KAIROS_TOKEN"`.

### List events

```bash
curl -H "$AUTH" "http://localhost:8080/api/v1/calendars/icloud/events?date_min=2026-08-14&date_max=2026-08-21"
```

`date_min` and `date_max` take a date or an ISO 8601 datetime (default: now to 30 days out; at most 366 days). `limit` is 1 to 1000 (default 100). The response has `total_count` (returned), `matched_count` (in range) and `truncated`. Recurring events are expanded into occurrences.

### Daily brief

```bash
curl -H "$AUTH" "http://localhost:8080/api/v1/calendars/icloud/today?timezone=America/New_York"
```

`timezone` defaults to the calendar's zone. Busy time counts only events that block time (not all-day, cancelled or free events), clipped to today. Free slots are gaps over 15 minutes. Results are cached for 5 minutes and refreshed on every write; if the source is down, the last brief is returned with `"stale": true`.

### Create an event

```bash
curl -X POST -H "$AUTH" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/calendars/icloud/events -d '{
    "title": "Team standup",
    "start_time": "2026-08-20T14:00:00-04:00",
    "end_time":   "2026-08-20T14:30:00-04:00",
    "location":   "Conf room B",
    "attendees":  [{"email": "alice@example.com", "name": "Alice"}],
    "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO,WE,FR"
  }'
```

Times must carry a UTC offset. `timezone` (IANA) overrides the zone the event is stored in. `status` is `confirmed`, `tentative` or `cancelled`. Unknown fields are rejected. Returns 201 with the event, including its `uid`. Attendees may get invitations from your calendar server.

### Update an event

```bash
curl -X PATCH -H "$AUTH" -H "Content-Type: application/json" \
  http://localhost:8080/api/v1/calendars/icloud/events/EVENT-UID \
  -d '{"start_time": "2026-08-20T15:00:00-04:00"}'
```

Only the fields you send change. Moving only `start_time` keeps the event's length. An empty string clears `description` or `location`. `attendees` replaces the list but keeps the RSVP of anyone who stays.

### Rules for changes

- A one-off event can't be changed or deleted from 15 minutes before it starts (`KAIROS_LOCK_WINDOW_MINUTES`), or once it has started.
- A recurring series can be renamed, moved to another location and so on at any time, but its times and its `recurrence_rule` can't be changed through the gateway; delete and recreate it.
- Deleting a recurring event deletes the whole series.

### Errors

Errors return `{"detail": "...", "error": "<code>"}`.

| Status | When |
|---|---|
| 400 | Invalid input (bad times, bad RRULE, unknown time zone) |
| 401 | Missing or invalid token |
| 403 | Calendar is read-only |
| 404 | Calendar or event not found |
| 409 | Event locked, recurring-time change, or the event changed on the server since it was read |
| 422 | Malformed body or unknown field |
| 502 | The CalDAV server rejected the gateway's credentials |
| 503 | The CalDAV server is unreachable, rate limiting, or its circuit breaker is open |

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `CALDAV_SOURCES` / `CALDAV_SOURCES_FILE` | | Calendar sources (JSON) |
| `KAIROS_SOURCES_FILE` | `/config/sources.yaml` | Calendar sources (YAML), used when `CALDAV_SOURCES` is unset |
| `DEFAULT_TIMEZONE` | `UTC` | Zone for sources that don't set one |
| `KAIROS_API_TOKENS` / `_FILE` | | Comma-separated bearer tokens |
| `KAIROS_PUBLIC_URL` | | Public https URL; needed for OAuth |
| `KAIROS_WORKOS_AUTHKIT_DOMAIN` | | Your AuthKit domain, for WorkOS login |
| `KAIROS_OIDC_ISSUER` | | Another identity provider's issuer URL |
| `KAIROS_OIDC_AUDIENCE` | `{public url}/mcp` | Accepted `aud` values, comma-separated |
| `KAIROS_OIDC_REQUIRED_SCOPES` | | Scopes every token must carry |
| `KAIROS_OIDC_ALLOWED_SUBJECTS` | any | Only these user IDs may connect; required with AuthKit |
| `KAIROS_OIDC_JWKS_URI` | discovered | Signing keys URL |
| `KAIROS_ALLOWED_HOSTS` | | Hostnames `/mcp` answers to (DNS-rebinding protection) |
| `KAIROS_LOCK_WINDOW_MINUTES` | `15` | Edit lock before a one-off event starts |
| `KAIROS_INSECURE_NO_AUTH` | `false` | Turn auth off entirely |
| `KAIROS_HOST`, `KAIROS_PORT` | `0.0.0.0`, `8080` | Listen address |
| `LOG_LEVEL` | `info` | |

Metrics: `caldav_request_duration_seconds`, `caldav_calendar_sync_errors_total`, `api_request_duration_seconds` (labelled by route), `api_errors_total`.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest                       # includes integration tests against a real Radicale server
pytest -m "not integration"  # unit and contract tests only
```

## Layout

```
src/kairos/          core: service, iCalendar handling, auth, settings
src/kairos/api/      REST and MCP adapters
src/kairos/sources/  CalDAV client and Google OAuth
tests/               unit and contract tests; tests/integration runs against Radicale
skill/               OpenAI-compatible tool definitions and chat commands
```

## License

Apache License 2.0, see [LICENSE](LICENSE).
