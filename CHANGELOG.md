# Changelog

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
