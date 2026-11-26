# Changelog

All notable changes to Kairos-MCP are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

A restructure into one installable package (`kairos`) with a shared calendar
core, a REST adapter and an MCP adapter. The gateway now serves MCP itself
over streamable HTTP at `/mcp`. 2.0 has breaking changes; read **Changed** and
**Removed** before you upgrade.

### Added
- MCP over streamable HTTP at `/mcp`, protected by the same API tokens, or by
  OAuth through WorkOS AuthKit (`KAIROS_WORKOS_AUTHKIT_DOMAIN`) or another
  identity provider (`KAIROS_OIDC_ISSUER`). Protected-resource metadata lets
  claude.ai custom connectors discover it. AuthKit requires an allow-list of
  user IDs.
- Provider plugins. A calendar source can name a `provider`; the default,
  `caldav`, covers every CalDAV server as before. Packages add providers
  through the `kairos.providers` entry point group. Kairos logs and skips a
  plugin that fails to load, and a read-only provider makes its calendars
  read-only. See [guides/providers.md](guides/providers.md).
- Per-caller services for multi-user hosts. `create_app(resolver=...)` takes
  an async function that returns the caller's own `CalendarService` from
  their verified token, for every REST request and MCP tool call, and
  `create_app(verifier=...)` takes your own token verifier. Self-hosted setups
  don't change. See [guides/embedding.md](guides/embedding.md).
- `get_event` (MCP) and `GET /api/v1/calendars/{id}/events/{uid}` (REST).
- `PATCH` as an alias for `PUT` on events.
- `timezone` on create sets the zone the event is stored in; `timezone` on
  MCP read tools returns local times.
- `list_events` responses include `matched_count` and `truncated`.
- The daily brief includes `generated_at` and `stale`, and falls back to the
  last good brief when a source is down.
- Sources can come from a YAML file or a JSON file (`CALDAV_SOURCES_FILE`).
- MCP tools carry read-only and destructive annotations.
- `kairos mcp-stdio` runs the MCP tools over stdio for local clients.
- Integration tests against a real CalDAV server (Radicale), plus CI.

### Changed
- **Breaking:** auth is on by default. Every endpoint except `/health` and
  `/metrics` needs `Authorization: Bearer <token>`. Set `KAIROS_API_TOKENS`
  (32+ characters each). With nothing configured, Kairos refuses every
  request; `KAIROS_INSECURE_NO_AUTH=true` restores the old open behaviour.
- **Breaking:** Kairos enforces `"writable": false`: writes to that calendar
  return 403.
- **Breaking:** request bodies reject unknown fields with 422. 1.x silently
  ignored `recurrence_rule` in an update; it is now an error.
- **Breaking:** the container runs `kairos serve`, the module layout under
  `src/` changed, and `pyproject.toml` replaces `requirements.txt`. Python
  3.12+.
- `total_calendar_time_minutes` in the daily brief counts only time that
  blocks the calendar (not all-day, cancelled or free events), clipped to
  today. 1.x summed raw durations.
- Kairos reads floating (zone-less) times in the source's `timezone`, not UTC.
- `api_request_duration_seconds` is labelled by route template instead of raw
  path, so event uids no longer create unbounded series.
- Upstream credential failures return 502 instead of 500 or 503.
- caldav upgraded from 1.3.9 to 3.x.

### Removed
- **Breaking:** `skill/mcp_server.py` and `skill/requirements.txt`. Point MCP
  clients at `https://<host>/mcp`, or run `kairos mcp-stdio` locally.
- The `caldav_rate_limit_remaining` metric, which nothing ever set.

### Fixed
- Updates rebuilt the event from scratch and lost alarms, RSVPs, categories,
  organizer and vendor properties. Updates now edit the stored event in place.
- Updates overwrote concurrent edits. They now send the server's ETag and
  return 409 when the event changed since Kairos read it.
- New events were written in UTC, so a recurring event shifted by an hour of
  local time after each DST change. Kairos now stores events in the
  calendar's zone with TZID and VTIMEZONE.
- Moving only `start_time` could leave `end_time` before it; the event keeps
  its length now.
- A recurring series that started in the past was permanently locked. You can
  now rename and edit a series (except its times) at any time.
- Events using DURATION instead of DTEND, or with no end at all, parsed with
  the wrong end time.
- Kairos retried writes on timeout, which could create duplicate events. It
  retries only reads now.
- `total_count` reported the returned count and hid that results were cut
  off; see `matched_count` and `truncated`.
- Free-slot lengths on DST-change days were off by an hour.

## [1.0.1] - 2026-08-15

First deployed release, running against real iCloud calendars.

### Added
- `skill/`: an MCP server (stdio transport) and an OpenAI-compatible
  `tools.json` covering all 7 gateway operations.
- `skill/commands.md`: ready-to-paste chat commands for Claude Desktop and
  claude.ai (`/agenda`, `/today`, `/calendars`, `/freeslot`).
- Google Calendar OAuth2 support (`auth_type: "oauth2"`), documented as the
  way to route Google through the shared gateway.
- Recurring event writes (RRULE) through `create_event`.

### Fixed
- `caldav.lib.error.RateLimitError` doesn't exist in caldav 1.3.9 (the pinned
  version) and crashed the app at import. Added a fallback.
- `get_properties()` with no arguments raised `TypeError` inside caldav 1.3.9
  even against a valid calendar and blocked startup. Kairos now logs it as a
  compatibility warning.
- A bad `calendar_path` (wrong slug format) now surfaces as a clean
  `CalDAVNotFoundError` through `PropfindError` handling, not an unhandled
  exception.
- The MCP server entry point used the old `run_stdio_async(read, write)`
  signature; the installed SDK takes no arguments.
- `list_calendars` returned the internal source ID as the display name (for
  example `"icloud"` instead of `"Calendar"`). Sources now carry a
  `display_name` from config.

### Documentation
- Corrected iCloud calendar-path discovery. iCloud uses account-shard URLs
  with UUID calendar paths
  (`https://p178-caldav.icloud.com/ACCOUNT_ID/calendars/UUID/`), not the older
  `/caldav/v2/email/calendar/slug/` format.
- A curl guide for every endpoint.
- The README now says plainly what the project is and isn't: a shared gateway
  for several agents, not the simplest way to connect one calendar to one
  model.

## [1.0.0]

The core gateway: CalDAV read and write, circuit breaker, structured logging,
Prometheus metrics and health checks. See the git history for the
task-by-task build log.
