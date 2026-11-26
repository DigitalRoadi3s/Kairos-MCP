# Changelog

## 2.0.0 (unreleased)

A restructure into one installable package (`kairos`) with a shared calendar
core, a REST adapter and an MCP adapter. MCP is now served by the gateway
itself over streamable HTTP at `/mcp`.

### Added
- **Provider plugins.** A calendar source can name a `provider`; the
  default, `caldav`, covers every CalDAV server as before. Packages add
  providers through the `kairos.providers` entry point group. A plugin that
  fails to load is logged and skipped, and a read-only provider makes its
  calendars read-only.
- **Per-caller services for multi-user hosts.** `create_app(resolver=...)`
  takes an async function that returns the caller's own `CalendarService`
  from their verified token, for every REST request and MCP tool call.
  Self-hosted setups are unchanged.

### Breaking
- **Auth is on by default.** Every endpoint except `/health` and `/metrics`
  needs `Authorization: Bearer <token>`. Set `KAIROS_API_TOKENS` (32+
  characters each). With nothing configured every request is refused;
  `KAIROS_INSECURE_NO_AUTH=true` restores the old open behaviour.
- `"writable": false` is now enforced: writes to that calendar return 403.
- Request bodies reject unknown fields with 422. Sending `recurrence_rule`
  in an update, which 1.x silently ignored, is now an error.
- `skill/mcp_server.py` and `skill/requirements.txt` are gone. Point MCP
  clients at `https://<host>/mcp`, or run `kairos mcp-stdio` locally.
- The container runs `kairos serve`; the module layout under `src/` changed
  and `requirements.txt` is replaced by `pyproject.toml`. Python 3.12+.
- `total_calendar_time_minutes` in the daily brief now counts only time that
  blocks the calendar (not all-day, cancelled or free events), clipped to
  today. 1.x summed raw durations.
- Floating (zone-less) times are read in the source's `timezone`, not UTC.
- The `caldav_rate_limit_remaining` metric is removed; it was never set.
  `api_request_duration_seconds` is labelled by route template instead of
  raw path, so event uids no longer create unbounded series.
- Upstream credential failures return 502 instead of 500 or 503.
- caldav upgraded from 1.3.9 to 3.x.

### Added
- MCP over streamable HTTP at `/mcp`, protected by the same API tokens, or by
  OAuth through WorkOS AuthKit (`KAIROS_WORKOS_AUTHKIT_DOMAIN`) or another
  identity provider (`KAIROS_OIDC_ISSUER`), with protected-resource metadata
  so claude.ai custom connectors can discover it. With AuthKit an allow-list
  of user IDs is required.
- `get_event` (MCP) and `GET /api/v1/calendars/{id}/events/{uid}` (REST).
- `PATCH` as an alias for `PUT` on events.
- `timezone` on create sets the zone the event is stored in; `timezone` on
  MCP read tools returns local times.
- `list_events` responses include `matched_count` and `truncated`.
- The daily brief includes `generated_at` and `stale`, and falls back to the
  last good brief when a source is down.
- Sources can be read from a YAML file or a JSON file (`CALDAV_SOURCES_FILE`).
- MCP tools carry read-only and destructive annotations.
- Integration tests against a real CalDAV server (Radicale), plus CI.

### Fixed
- Updates rebuilt the event from scratch and lost alarms, RSVPs, categories,
  organizer and vendor properties. Updates now edit the stored event in place.
- Updates overwrote concurrent edits. They now use the server's ETag and
  return 409 when the event changed since it was read.
- New events were written in UTC, so a recurring event shifted by an hour of
  local time after each DST change. Events are now stored in the calendar's
  zone with TZID and VTIMEZONE.
- Moving only `start_time` could leave `end_time` before it; the event's
  length is now kept.
- A recurring series that started in the past was permanently locked.
  Series can now be renamed and edited (except their times) at any time.
- Events using DURATION instead of DTEND, or with no end at all, parsed with
  the wrong end time.
- Writes were retried on timeout, which could create duplicate events.
  Only reads are retried now.
- `total_count` reported the returned count while hiding that results were
  cut off; see `matched_count` and `truncated`.
- Free-slot lengths on DST-change days were off by an hour.

## 1.0.1 — 2026-08-15

First deployed release, running on Docker01 with real iCloud calendars connected.

### Fixed
- `caldav.lib.error.RateLimitError` doesn't exist in caldav 1.3.9 (the pinned
  version) — was crashing the app at import time. Added a defensive fallback.
- `get_properties()` with no arguments raises `TypeError` internally on
  caldav 1.3.9 even against a valid calendar — was blocking startup entirely.
  Now caught and logged as a compat warning instead of a hard failure.
- A genuine bad `calendar_path` (wrong slug format) now surfaces as a clean
  `CalDAVNotFoundError` via `PropfindError` handling, instead of an unhandled
  exception.
- MCP server entry point used the old `run_stdio_async(read, write)` signature;
  the installed SDK version takes no arguments. Fixed to `run_stdio_async()`.
- `list_calendars` returned the internal source ID as the display name
  (e.g. `"icloud"` instead of `"Calendar"`). `CalDAVSourceClient` now carries
  a proper `display_name`, threaded through from config.

### Added
- `skill/` — MCP server (stdio transport) and OpenAI-compatible `tools.json`,
  covering all 7 gateway operations.
- `skill/commands.md` — ready-to-paste Claude Desktop / claude.ai chat slash
  commands (`/agenda`, `/today`, `/calendars`, `/freeslot`).
- Google Calendar OAuth2 support (`auth_type: "oauth2"`), documented as the
  path for routing Google through the shared gateway specifically — not a
  general "how to connect Google" recommendation, since Google's own MCP
  server is simpler for Claude-only use.
- Recurring event write support (RRULE) via `create_event`.

### Documentation
- iCloud calendar-path discovery instructions corrected — iCloud uses
  account-shard URLs with UUID-based calendar paths
  (`https://p178-caldav.icloud.com/ACCOUNT_ID/calendars/UUID/`), not the
  older `/caldav/v2/email/calendar/slug/` format.
- Full curl usage guide added for every endpoint.
- README repositioned to be explicit about what this project is and isn't —
  a multi-agent shared gateway, not the simplest way to connect one calendar
  to one model.

### Known gaps
- Gateway currently has only iCloud calendars configured. Events living on
  Google Calendar or Microsoft 365 (if added as separate accounts in a
  device's Calendar app rather than natively in iCloud) won't appear until
  those sources are added.
- Nextcloud and Google CalDAV sources are implemented and tested but not
  yet running in the live deployment.

## 1.0.0 — Initial build

Core gateway (Phases 1–3): CalDAV read/write, circuit breaker, structured
logging, Prometheus metrics, health checks. See git history for the full
task-by-task build log.
