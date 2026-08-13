# Product Requirements Document: CalDAV to REST Connector

## Executive Summary

**Product Name:** CalDAV REST Gateway  
**Purpose:** Expose CalDAV calendar systems (iCloud, Google Calendar, Nextcloud, etc.) as a simple REST API that Claude can call natively.  
**Deployment Model:** Containerized microservice (Docker) running on the user's homelab.  
**Primary Use Case:** Enable Claude to read, query, and manipulate iCloud Calendar events without direct CalDAV protocol handling.

---

## Problem Statement

CalDAV is a protocol standard for calendar access, but it requires:
- Low-level protocol understanding (WebDAV, iCalendar format parsing)
- Complex authentication (often digest or OAuth with calendar provider)
- Stateful connection management
- Manual parsing and transformation of iCalendar objects

Claude has no native CalDAV support and cannot call arbitrary socket-based protocols. This blocks Claude from accessing iCloud Calendar, Google Calendar (via CalDAV), and other CalDAV-compliant systems.

**Desired State:** Claude should be able to query, create, and update calendar events via a simple REST API backed by a CalDAV source.

---

## Goals & Success Metrics

### Goals
1. Enable Claude to read all events from a CalDAV calendar (filtered by date range, title, or attendee)
2. Allow Claude to create new calendar events via a REST endpoint
3. Allow Claude to update/delete existing events
4. Provide transparent authentication so the gateway handles CalDAV credentials internally
5. Support multiple calendar sources in a single container
6. Graceful error handling and retry logic for network/auth failures
7. Minimal resource footprint for homelab deployment

### Success Metrics
- **Latency:** < 500ms for event list queries (< 1000 events)
- **Availability:** 99% uptime for 30-day periods
- **Accuracy:** 100% fidelity round-trip (create → read back identical data)
- **Scope Coverage:** Supports iCloud, Google Calendar (CalDAV), Nextcloud; extensible to Davical, etc.
- **Ease of Setup:** Single `docker run` command with env vars; no manual config file editing

---

## Out of Scope

- Web UI for calendar management (REST API only)
- Two-way sync daemon (no continuous mirroring)
- Support for CalDAV extensions beyond RFC 4791 (e.g., CalDAV scheduling)
- Multi-tenant account isolation (single-user homelab tool)
- Attendee/invite management
- Recurring event manipulation (read-only support for recurring; create-only simple events)
- Timezone auto-detection (client must specify)

---

## User Personas & Use Cases

### Persona: Eric (Platform Engineer, Daily Brief Use)
**Scenario:** Eric wants Claude to include his calendar in his daily morning brief—showing today's meetings, blocked time, and key attendees.

**Flow:**
1. At 7 AM, Eric's homelab runs: `GET /api/v1/calendars/icloud/daily`
2. Container returns optimized JSON: today's events sorted by time, with duration and attendee summary
3. Claude fetches this data as part of morning brief generation
4. Morning brief displays: "You have 5 meetings today; 4 hours of calendar time. Key attendees: Alice (3x), Bob (2x). First meeting: Team Standup at 9 AM."
5. Eric can ask Claude: "Do I have time for a 1-hour deep work session this afternoon?" Claude analyzes gaps and suggests slots

---

## Functional Requirements

### FR-1: Calendar Source Configuration
- Support multiple calendar sources via environment variables or config file
- Each source specifies:
  - CalDAV server URL (e.g., `https://caldav.icloud.com/`)
  - Username/email
  - Password or auth token
  - Calendar ID/path (e.g., `/caldav/v2/user@icloud.com/calendar/personal/`)
  - Display name
- Validate connectivity on container startup; fail gracefully if unreachable

**Implementation Hint:** Store sources in `config.yaml` mounted as volume, or parse from `CALDAV_SOURCES` env JSON.

### FR-2: List Events
**Endpoint:** `GET /api/v1/calendars/{calendar_id}/events`

**Query Parameters:**
- `date_min` (ISO 8601, optional): Start of date range (default: today)
- `date_max` (ISO 8601, optional): End of date range (default: +30 days)
- `limit` (int, optional, default 100): Max events to return
- `title` (string, optional): Filter by event title substring
- `attendee_email` (string, optional): Filter by attendee email

**Response:** 
```json
{
  "calendar_id": "icloud",
  "calendar_name": "Personal",
  "total_count": 5,
  "events": [
    {
      "uid": "uuid-from-caldav",
      "title": "Team Standup",
      "description": "Daily standup",
      "start_time": "2026-08-12T09:00:00Z",
      "end_time": "2026-08-12T09:30:00Z",
      "all_day": false,
      "attendees": [
        {"email": "alice@company.com", "name": "Alice", "status": "accepted"},
        {"email": "bob@company.com", "name": "Bob", "status": "tentative"}
      ],
      "location": "Room 1",
      "recurrence_rule": null,
      "last_modified": "2026-08-11T15:30:00Z",
      "status": "confirmed"
    }
  ]
}
```

**Error Responses:**
- `400`: Invalid date range or query params
- `404`: Calendar not found
- `401`: Auth failure with CalDAV source
- `503`: CalDAV server unreachable

### FR-3: Create Event
**Endpoint:** `POST /api/v1/calendars/{calendar_id}/events`

**Request Body:**
```json
{
  "title": "Architecture Review",
  "description": "Review microservices design",
  "start_time": "2026-08-12T14:00:00Z",
  "end_time": "2026-08-12T15:00:00Z",
  "all_day": false,
  "location": "Conference Room A",
  "attendees": [
    {"email": "alice@company.com", "name": "Alice"}
  ],
  "timezone": "America/New_York",
  "status": "confirmed"
}
```

**Response:**
```json
{
  "uid": "new-uuid-generated",
  "title": "Architecture Review",
  "start_time": "2026-08-12T14:00:00Z",
  "end_time": "2026-08-12T15:00:00Z",
  "created_at": "2026-08-11T16:00:00Z",
  "link": "/api/v1/calendars/icloud/events/new-uuid-generated"
}
```

**Validation:**
- `title` required, max 200 chars
- `start_time` and `end_time` required, must be valid ISO 8601
- `start_time` < `end_time`
- If `all_day=true`, times are ignored; event spans full day
- `timezone` optional (defaults to UTC or configured default)

**Error Responses:**
- `400`: Validation failure (missing title, bad times, etc.)
- `409`: Conflict (e.g., attendee cannot be scheduled)
- `401`: Auth failure
- `503`: CalDAV server error

### FR-4: Update Event
**Endpoint:** `PUT /api/v1/calendars/{calendar_id}/events/{uid}`

**Request Body:** Same as create, but all fields optional (PATCH semantics).

**Response:** Updated event object (same as GET).

**Constraints:**
- Only future events can be modified (events within 15 min of start time are locked)
- Cannot change recurrence rule (recurring events read-only)

### FR-5: Delete Event
**Endpoint:** `DELETE /api/v1/calendars/{calendar_id}/events/{uid}`

**Response:**
```json
{
  "uid": "uuid",
  "status": "deleted",
  "deleted_at": "2026-08-11T16:00:00Z"
}
```

**Constraints:**
- Only future events can be deleted (same 15-min lockout)

### FR-6: Daily Brief Endpoint (Optimized for Morning Brief)
**Endpoint:** `GET /api/v1/calendars/{calendar_id}/today`

**Purpose:** Single optimized call for daily brief generation. Returns today's events with analysis (gaps, duration, attendee summary).

**Query Parameters:**
- None (always today, user's local timezone)
- Optional: `timezone` (default: configured DEFAULT_TIMEZONE) to compute "today" in user's zone

**Response:**
```json
{
  "calendar_id": "icloud",
  "calendar_name": "Personal",
  "date": "2026-08-12",
  "timezone": "America/New_York",
  "summary": {
    "total_events": 5,
    "total_calendar_time_minutes": 240,
    "first_event_start": "2026-08-12T09:00:00-04:00",
    "last_event_end": "2026-08-12T17:30:00-04:00",
    "top_attendees": [
      {"email": "alice@company.com", "name": "Alice", "count": 3},
      {"email": "bob@company.com", "name": "Bob", "count": 2}
    ],
    "free_slots": [
      {
        "start": "2026-08-12T09:00:00-04:00",
        "end": "2026-08-12T10:00:00-04:00",
        "duration_minutes": 60
      },
      {
        "start": "2026-08-12T12:30:00-04:00",
        "end": "2026-08-12T14:00:00-04:00",
        "duration_minutes": 90
      }
    ]
  },
  "events": [
    {
      "uid": "uuid-1",
      "title": "Team Standup",
      "start_time": "2026-08-12T09:00:00-04:00",
      "end_time": "2026-08-12T09:30:00-04:00",
      "duration_minutes": 30,
      "location": "Room 1",
      "attendees": [
        {"email": "alice@company.com", "name": "Alice", "status": "accepted"},
        {"email": "bob@company.com", "name": "Bob", "status": "tentative"}
      ],
      "is_all_day": false,
      "is_busy": true
    },
    {
      "uid": "uuid-2",
      "title": "1:1 with Alice",
      "start_time": "2026-08-12T10:00:00-04:00",
      "end_time": "2026-08-12T10:30:00-04:00",
      "duration_minutes": 30,
      "location": null,
      "attendees": [
        {"email": "alice@company.com", "name": "Alice", "status": "accepted"}
      ],
      "is_all_day": false,
      "is_busy": true
    }
  ]
}
```

**Behavior:**
- Sorted by start time (earliest first)
- Includes all-day events
- Computes free time slots between events (gaps > 15 minutes)
- Aggregates attendees across all events
- All times in user's local timezone
- Cached locally for 5 minutes (avoid hitting iCloud every minute)

**Use in Daily Brief:**
```
Eric's Calendar Summary
━━━━━━━━━━━━━━━━━━━━━━━
📅 Tuesday, August 12
Total meeting time: 4 hours (15% of working day)
Free time: 3 blocks, largest gap is 90 minutes (12:30–2:00 PM)

👥 Key attendees today:
  Alice (3 meetings), Bob (2 meetings)

⏰ Schedule:
  9:00–9:30   Team Standup         [30 min] Room 1
  9:30–10:00  ⏳ FREE
  10:00–10:30 1:1 with Alice       [30 min]
  10:30–12:30 ⏳ FREE (2 hours) ← Best deep work slot
  12:30–1:30  Lunch with stakeholders [60 min] Downtown
  1:30–2:00   ⏳ FREE
  2:00–3:30   Design review + 1:1 w/ Bob [90 min] Conference Room
  3:30–5:30   ⏳ FREE
  5:30–6:00   Wrap-up standup      [30 min]
```

**Error Responses:**
- `400`: Invalid timezone
- `404`: Calendar not found
- `503`: CalDAV unreachable

### FR-7: Health & Admin Endpoints
- `GET /health`: Basic liveness probe
  ```json
  {
    "status": "healthy",
    "calendars": {
      "icloud": {
        "status": "connected",
        "last_sync": "2026-08-11T15:55:00Z",
        "last_error": null,
        "rate_limit_remaining": 875,
        "cache_hit_rate": 0.85
      }
    }
  }
  ```
- `GET /api/v1/calendars`: List all configured calendars
  ```json
  {
    "calendars": [
      {
        "id": "icloud",
        "name": "Personal",
        "color": "#FF2968",
        "writable": true,
        "timezone": "America/New_York"
      }
    ]
  }
  ```
- `GET /metrics`: Prometheus metrics (includes cache hit rate, query latency, iCloud rate limit)

---

## Platform: Linux + iCloud Requirements

### iCloud CalDAV Gotchas (Locked-In)

**1. App-Specific Passwords Required**
- iCloud does NOT support user account passwords over CalDAV (security restriction)
- You MUST generate an **app-specific password** in iCloud Settings → Security
- This password is 16 characters, auto-formatted with hyphens (e.g., `XXXX-XXXX-XXXX-XXXX`)
- Container startup MUST validate this during health check; fail loudly if auth fails
- Log in PRD: "iCloud app-specific password required; generate at icloud.com/account/security"

**2. CalDAV Server URL for iCloud**
- Primary endpoint: `https://caldav.icloud.com/`
- Calendar path format: `/caldav/v2/{email}/calendar/{calendar_id}/`
- Default personal calendar often: `/caldav/v2/user@icloud.com/calendar/personal/`
- Some iCloud setups use UUID-based calendar IDs (requires discovery step)
- Container MUST support calendar discovery (PROPFIND) to find available calendars
- Requirement: Add `GET /api/v1/calendars/{id}/discover` endpoint to list calendars under an iCloud account

**3. TLS Certificate Validation**
- iCloud enforces strict TLS 1.2+ with valid certificates
- Linux systems MUST have up-to-date CA certificates installed
- Dockerfile requirement: Alpine must include `ca-certificates` package
- On old/minimal Linux, TLS will fail silently → add explicit cert validation logging

**4. Rate Limiting & Throttling**
- iCloud applies rate limits (typically 1000 requests/hour per account)
- Container MUST implement request queuing & backoff (exponential, max 30s between retries)
- Log rate-limit headers from iCloud; expose in `/health` endpoint
- Requirement: Add `caldav_rate_limit_remaining` metric to Prometheus

**5. Connection Stability**
- iCloud CalDAV can be flaky during peak hours (Apple infrastructure)
- Container MUST NOT crash on transient failures
- Requirement: Connection pooling with keep-alive; timeout 30s per request
- Implement circuit breaker: after 5 consecutive failures in 60s, return 503 immediately for 5 min

**6. Timezone Handling**
- iCloud returns all times in UTC by default, but stores local timezone in event
- VTIMEZONE blocks in iCalendar may not parse correctly on some Linux systems
- Requirement: Strip VTIMEZONE; normalize all times to UTC; store original timezone in metadata
- Test with America/New_York, Europe/London, Asia/Tokyo

### Linux-Specific Requirements

**1. Container Base Image**
- Use `python:3.11-alpine` (lightweight, ~ 50 MB)
- OR `python:3.11-slim` if Alpine has library compatibility issues
- MUST include: `ca-certificates`, `openssl`, `libffi-dev` (for TLS and CalDAV deps)
- Do NOT use `python:3.11` (full OS; bloated)

**2. User & Permissions**
- Run as non-root user (e.g., `appuser:appuser`)
- Container must NOT run as `root`
- Mounted secrets (if using Docker secrets) must have mode `0600`

**3. Signal Handling**
- Linux containers receive SIGTERM for graceful shutdown
- Container MUST trap SIGTERM, close CalDAV connections, exit within 10s
- Requirement: Add `signal.signal(signal.SIGTERM, shutdown_handler)` in Python

**4. Networking on Linux**
- If running on homelab with custom DNS or split-brain DNS, provide DNS override
- Requirement: Support `DNS_SERVERS` env var (comma-separated IPs)
- Example: `DNS_SERVERS=192.168.1.1,8.8.8.8`

**5. Logs to stdout**
- Docker logs must capture all output via stdout/stderr (no file logging)
- Requirement: All logs go to `/dev/stdout`, no `/var/log/` files

**6. Volume Mounts**
- Config should be read-only: `-v /path/to/config.yaml:/config/sources.yaml:ro`
- If using Docker secrets, mount to `/run/secrets/caldav_password`
- No persistent state needed (stateless design)

---

## Non-Functional Requirements

### NFR-1: Security
- **Credentials Management:** Store CalDAV credentials only in env vars or mounted secrets, never in logs. iCloud requires app-specific password (not account password).
- **HTTPS:** TLS 1.2+ mandatory for iCloud CalDAV; container must validate certificates. Local REST endpoint may use HTTP (firewall-protected).
- **API Authentication:** None required for homelab (firewall-protected), but support Bearer token auth if needed
- **Rate Limiting:** Implement request queuing for iCloud (rate-limited to ~1000 req/hr); expose remaining quota in health check and metrics

### NFR-2: Reliability & Daily Brief Optimization
- **Retry Logic:** On CalDAV connection failure, retry with exponential backoff (3 attempts, 2s–10s delays)
- **Timeout:** Set read/write timeout of 30s for all CalDAV operations
- **Connection Pooling:** Reuse HTTP connections to CalDAV server (pool size: 10)
- **Caching Strategy (Daily Brief):**
  - Cache today's events for 5 minutes (reduces iCloud hits during morning brief)
  - Cache calendar list for 1 hour (metadata change infrequently)
  - Invalidate cache on write operations (create/update/delete events)
  - Expose cache hit rate in `/health` and `/metrics`
- **Graceful Degradation:** If CalDAV becomes unavailable, return 503 with descriptive error + last cached data (if available, for daily brief). Don't crash container.

### NFR-3: Performance
- **Latency Target:** < 500ms for typical event list query (< 1000 events)
- **Memory:** < 200 MB at idle, < 500 MB under load (listing 5000 events)
- **Startup Time:** Ready to serve requests within 5 seconds

### NFR-4: Observability
- **Logging:** Structured JSON logs (timestamp, level, event, calendar_id, duration)
  - Log CalDAV connection attempts (success/failure)
  - Log API request/response (method, endpoint, status, latency)
  - Do NOT log sensitive data (passwords, tokens, email bodies)
- **Metrics:** Expose Prometheus-style metrics at `GET /metrics`
  - `caldav_request_duration_seconds` (histogram by method, path)
  - `caldav_calendar_sync_errors_total` (counter by calendar_id)
  - `api_request_duration_seconds` (histogram by method, path)
  - `api_errors_total` (counter by status_code)

### NFR-5: Deployment
- **Container Image:** Alpine-based Linux, < 200 MB
- **Runtime:** Python 3.11+ or Go 1.21+
- **Dependencies:** Minimal (caldav library, FastAPI/http, prometheus client)
- **Configuration:** Via environment variables (12-factor app)
  - `CALDAV_SOURCES`: JSON array of calendar configs
  - `LISTEN_PORT`: REST API port (default 8080)
  - `LOG_LEVEL`: debug, info, warn, error (default info)
  - `DEFAULT_TIMEZONE`: Timezone for all-day events (default UTC)

---

## Technical Architecture

### High-Level Flow
```
Claude API Call
    ↓
REST Gateway (Container)
    ├─ Auth (token validation, if enabled)
    ├─ Request parsing & validation
    ├─ CalDAV client (cached connection pool)
    ├─ iCalendar parsing (event extraction)
    ├─ JSON transformation
    └─ Response
    ↓
Claude (processes JSON, returns to user)
```

### Core Components

1. **HTTP Server:** FastAPI (Python) or Fiber (Go)
   - Handles REST endpoints
   - Middleware for logging, error handling
   - OpenAPI schema generation (auto-documentation)

2. **CalDAV Client:** 
   - Python: `caldav` library (well-maintained, supports iCloud)
   - Go: Custom implementation or community library (e.g., `go-caldav`)
   - Maintains connection pool & auth session

3. **iCalendar Parser:**
   - Convert iCalendar (RFC 5545) to JSON
   - Handle recurring events (expand within date range)
   - Normalize timezones

4. **Configuration Manager:**
   - Load sources from env or config file on startup
   - Validate connectivity
   - Track connection state

5. **Logging & Metrics:**
   - Structured logging to stdout (JSON format)
   - Prometheus metrics exporter

### Sequence Diagram: List Events
```
Claude                         Gateway                  iCloud CalDAV
  │                               │                          │
  ├─ GET /calendars/icloud/events?date_min=...─────────────>│
  │                               │                          │
  │                               ├─ PROPFIND calendar──────>│
  │                               │<─ Calendar props────────┤
  │                               │                          │
  │                               ├─ REPORT (date-range)───>│
  │                               │<─ iCalendar objects────┤
  │                               │                          │
  │                               ├─ Parse, transform       │
  │                               │                          │
  │<─────── 200 OK + events JSON ─────────────────────────────┤
  │                               │                          │
```

---

## API Specification

### Base URL
```
http://localhost:8080/api/v1
```

### Authentication
None required for homelab. For production use, add bearer token support:
```
Authorization: Bearer <token>
```

### Rate Limiting
None. (Single-user homelab assumption.)

### Pagination
For large event lists, support cursor-based pagination:
```
GET /api/v1/calendars/{id}/events?limit=50&cursor=<opaque_token>
```

### Error Responses
All errors return JSON:
```json
{
  "error": "invalid_request",
  "message": "date_min must be before date_max",
  "status_code": 400,
  "timestamp": "2026-08-11T16:00:00Z"
}
```

### Content-Type
All requests/responses: `application/json; charset=utf-8`

---

## Implementation Roadmap

### Phase 1: MVP (2 weeks)
- [ ] FastAPI skeleton with config loading
- [ ] CalDAV client setup (test with iCloud)
- [ ] GET /calendars endpoint (list sources)
- [ ] GET /calendars/{id}/events endpoint (with date range filtering)
- [ ] **GET /calendars/{id}/today endpoint (daily brief optimized)**
  - Analyze gaps, attendee summary, free slots
  - Implement 5-min cache for today's events
- [ ] Basic error handling & logging
- [ ] Docker image build
- [ ] README with setup instructions + daily brief example

**Deliverable:** Single-calendar proof-of-concept; read-only. Daily brief fully functional.

### Phase 2: Write Support (1 week)
- [ ] POST /calendars/{id}/events (create)
- [ ] PUT /calendars/{id}/events/{uid} (update)
- [ ] DELETE /calendars/{id}/events/{uid} (delete)
- [ ] Input validation & error messages
- [ ] iCalendar generation (write back to CalDAV)

**Deliverable:** Full CRUD for events.

### Phase 3: Polish & Ops (1 week)
- [ ] Health check endpoint & connection validation
- [ ] Prometheus metrics export
- [ ] Structured JSON logging
- [ ] Documentation & OpenAPI schema
- [ ] Docker Compose example with env file
- [ ] Test coverage (unit & integration)

**Deliverable:** Production-ready container image.

### Phase 4: Advanced (Future)
- [ ] Support for multiple calendar sources (Google, Nextcloud, etc.)
- [ ] Recurring event expansion
- [ ] Attendee management
- [ ] Calendar color & category metadata
- [ ] Web UI dashboard (optional)

---

## Technology Stack Recommendation

### Primary Choice: Python + FastAPI (Optimized for iCloud)
**Pros:**
- `caldav` library has **proven iCloud support** (includes iCloud-specific auth handling)
- FastAPI is lightweight & fast (< 50ms overhead)
- Easy to extend & debug
- Wide ecosystem (logging, metrics, testing)
- `requests` library handles TLS certificate verification properly on Linux

**Cons:**
- Slightly heavier memory footprint than Go
- Requires Python runtime

**Stack:**
- Runtime: Python 3.11 (Alpine-based)
- Web: FastAPI + Uvicorn
- CalDAV: `caldav` (>= 1.0.0, must support iCloud app-specific passwords)
- HTTP: `requests` with `urllib3` (for connection pooling & TLS validation)
- iCalendar: `icalendar` library (>= 5.0.0 for timezone handling)
- Logging: `structlog` + JSON formatter
- Metrics: `prometheus_client`
- Validation: Pydantic v2
- Testing: `pytest` + `pytest-asyncio`

**requirements.txt:**
```
# Core HTTP & CalDAV
FastAPI==0.104.1
uvicorn[standard]==0.24.0
caldav==1.3.0
requests==2.31.0
urllib3==2.0.7

# iCalendar parsing (MUST support timezone handling)
icalendar==5.0.11
python-dateutil==2.8.2
pytz==2023.3

# Validation & data
pydantic==2.4.2
pydantic-settings==2.0.3

# Logging (structured JSON)
structlog==23.2.0
python-json-logger==2.0.7

# Metrics (Prometheus)
prometheus-client==0.18.0

# Testing
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-cov==4.1.0
httpx==0.25.1  # For testing async endpoints

# Optional: debugging
python-dotenv==1.0.0
```

**Critical iCloud-Specific Notes:**
- `caldav >= 1.0.0`: Includes proper iCloud CalDAV support with app-specific password handling
- `requests >= 2.31.0`: Essential for proper SSL/TLS certificate validation on Linux
- `icalendar >= 5.0.0`: Handles VTIMEZONE blocks correctly (older versions have bugs)
- `urllib3`: Enables connection pooling for performance (automatic via requests)

### Alternative: Go
**Pros:**
- Minimal binary size (< 50 MB)
- Superior performance & memory usage
- Single deployment unit (no runtime required)

**Cons:**
- CalDAV library ecosystem less mature
- More boilerplate code

If you want maximum efficiency, Go is viable; Python is recommended for speed-to-market.

---

## Testing Strategy

### Unit Tests
- iCalendar parsing (recurring events, timezones, edge cases)
- Request validation (date ranges, required fields)
- Error handling & retries

### Integration Tests
- CalDAV client connectivity (test against public CalDAV server or mock)
- Full event lifecycle (create → read → update → delete)
- Concurrent request handling

### E2E Tests
- Docker container startup & health check
- REST API full workflows
- iCloud Calendar integration (real account, optional)

---

## Success Criteria (Phase 1 MVP)

✅ Claude can query iCloud Calendar events via REST  
✅ Events returned as valid JSON with essential fields (title, start, end, attendees)  
✅ Date filtering works correctly (events outside range excluded)  
✅ **Daily brief endpoint (`/today`) returns optimized calendar data**  
✅ **Free time slots accurately computed (gaps > 15 minutes)**  
✅ **Attendee aggregation works across all events**  
✅ **Today's events cached for 5 minutes (verify cache hit rate in `/metrics`)**  
✅ Container runs with single `docker run` command  
✅ Startup < 5 seconds  
✅ Latency < 500ms for typical calendar (including daily brief)  
✅ All CalDAV auth errors logged & returned gracefully  
✅ **Daily brief data persists if iCloud briefly unavailable**  

---

## Known Risks & Mitigations (Linux + iCloud Specific)

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **iCloud app-specific password mandatory** | High | Document in setup; validate password format on startup; log clear error if auth fails |
| **TLS certificate validation fails on old Linux** | High | Lock CA bundle in Docker image; add explicit cert path logging; test on Alpine |
| **iCloud CalDAV intermittent flakiness** | High | Implement exponential backoff (2s–30s); circuit breaker after 5 failures; retry queue |
| **Daily brief cache invalidation race condition** | High | Write events first, then invalidate cache; cache timestamp included in response |
| **Rate limiting (1000 req/hr quota)** | Medium | Request queuing; cache results (today: 5 min, calendar list: 1 hr); monitor X-RateLimit-Remaining header; expose metric |
| **Calendar ID discovery required** | Medium | Provide discovery script; require calendar_path in config; validate on startup |
| **Timezone handling (VTIMEZONE parsing)** | Medium | Use `icalendar >= 5.0.0`; test with America/New_York, Europe/London, Asia/Tokyo; normalize today's date to local TZ |
| **iCloud CalDAV may change auth flow** | Medium | Monitor iCloud status page; design abstraction layer in CalDAV client; avoid hardcoding auth |
| **Large calendars (> 10k events) slow to query** | Medium | Implement server-side date filtering; paginate results; cache with 5-min TTL |
| **Signal handling on container shutdown** | Medium | Trap SIGTERM; close CalDAV connections gracefully; exit within 10s |
| **Linux DNS issues (split-brain DNS)** | Low | Support DNS_SERVERS env var; test in target homelab environment |
| **Daily brief stale during morning peak (high load)** | Low | Cache hits during high concurrency; add cache stats to `/health` for monitoring |

---

## Appendix A: Docker Setup for Linux + iCloud

### Step 1: Generate iCloud App-Specific Password
1. Go to https://appleid.apple.com/account/security
2. Sign in with your Apple ID
3. Under "App-specific passwords", click "Generate"
4. Select "Other" and enter a label like "CalDAV Gateway"
5. Copy the 16-character password (e.g., `XXXX-XXXX-XXXX-XXXX`)
6. **Store securely**; you'll need it for the container

### Step 2: Discover Your iCloud Calendar ID
Run this quick Python script to find your calendar ID (you'll need it for the container):

```python
#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from caldav import DAVClient

email = input("Enter iCloud email: ")
password = input("Enter app-specific password: ")

client = DAVClient(
    url="https://caldav.icloud.com/",
    username=email,
    password=password
)

calendars = client.principal().calendars()
for cal in calendars:
    print(f"ID: {cal.name}")
    print(f"Display Name: {cal.get_properties()['{DAV:}displayname']}")
    print(f"Path: {cal.url}")
    print("---")
```

This discovers your actual calendar IDs (may not be "personal"; could be UUID).

### Step 3: Dockerfile for Linux

```dockerfile
FROM python:3.11-alpine

# Install runtime dependencies (TLS, libffi for CalDAV)
RUN apk add --no-cache \
    ca-certificates \
    openssl \
    libffi-dev \
    gcc \
    musl-dev

# Create non-root user
RUN addgroup -g 1000 appuser && adduser -D -u 1000 -G appuser appuser

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies (no cache to reduce image size)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=5)" || exit 1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Step 4: Docker Compose for Linux Homelab

Create `docker-compose.yml`:

```yaml
version: '3.9'

services:
  caldav-gateway:
    image: caldav-rest-gateway:latest
    container_name: caldav-gateway
    ports:
      - "8080:8080"
    environment:
      # iCloud CalDAV configuration
      CALDAV_SOURCES: |
        [
          {
            "id": "icloud",
            "name": "iCloud Personal",
            "url": "https://caldav.icloud.com/",
            "username": "your-email@icloud.com",
            "password": "xxxx-xxxx-xxxx-xxxx",
            "calendar_path": "/caldav/v2/your-email@icloud.com/calendar/personal/",
            "writable": true
          }
        ]
      LOG_LEVEL: "info"
      DEFAULT_TIMEZONE: "America/New_York"
      DNS_SERVERS: "192.168.1.1,8.8.8.8"
    restart: unless-stopped
    networks:
      - homelab
    # Optional: use Docker secrets for password (more secure)
    # secrets:
    #   - icloud_password

networks:
  homelab:
    driver: bridge

# secrets:
#   icloud_password:
#     file: ./secrets/icloud_password.txt
```

### Step 5: Run Container

```bash
# Build image
docker build -t caldav-rest-gateway:latest .

# Run with Docker Compose
docker-compose up -d

# Check logs
docker-compose logs -f caldav-gateway

# Test health endpoint
curl http://localhost:8080/health

# Test daily brief (quick test for morning brief setup)
curl "http://localhost:8080/api/v1/calendars/icloud/today"

# Test event range query
curl "http://localhost:8080/api/v1/calendars/icloud/events?date_min=2026-08-12&date_max=2026-08-19"
```

### Step 6: Integrate with Claude Morning Brief

Create a Python script to call the daily brief endpoint:

```python
import requests
import json
from datetime import datetime

def get_calendar_brief(api_url="http://localhost:8080"):
    """Fetch today's calendar for morning brief."""
    try:
        response = requests.get(
            f"{api_url}/api/v1/calendars/icloud/today",
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        return {"error": str(e)}

# Example usage
brief = get_calendar_brief()
print(json.dumps(brief, indent=2))

# In Claude, pass this JSON to your morning brief skill:
# "Include my calendar: {daily_brief_json}"
```

### Step 7: Use in Claude Morning Brief

In your morning brief (or ask Claude to call the endpoint):

```python
# Inside Claude morning brief skill
import requests

def fetch_calendar():
    response = requests.get("http://localhost:8080/api/v1/calendars/icloud/today")
    return response.json()

calendar = fetch_calendar()
print(f"📅 {calendar['summary']['total_events']} meetings today")
print(f"⏱ {calendar['summary']['total_calendar_time_minutes']} minutes of meetings")
print(f"👥 Top attendees: {', '.join([a['name'] for a in calendar['summary']['top_attendees']])}")
print("\nToday's schedule:")
for event in calendar['events']:
    print(f"  {event['start_time'][:5]}–{event['end_time'][:5]}: {event['title']}")
```

### Step 8: Use in Claude Conversations

```
User: Check my calendar—do I have time for a 1-hour meeting this afternoon?

Claude: [calls GET /api/v1/calendars/icloud/today]

Based on your calendar, you have a 90-minute free slot from 12:30–2:00 PM
and another 2-hour gap from 3:30–5:30 PM. I'd suggest the 3:30 PM slot
to give time for lunch. Shall I create a meeting?
```

---

## Appendix B: Troubleshooting iCloud CalDAV on Linux

### Issue 1: TLS Certificate Verification Failed

**Error:**
```
requests.exceptions.SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
```

**Cause:** Container missing CA certificates or outdated cert bundle.

**Fix:**
1. Ensure `ca-certificates` is installed in Dockerfile
2. Update CA bundle: `RUN update-ca-certificates` (Alpine)
3. Check if system certs are available: `ls -la /etc/ssl/certs/ca-bundle.crt`
4. For older Alpine: `RUN apk add --no-cache ca-certificates && update-ca-certificates`

### Issue 2: Authentication Failed with App-Specific Password

**Error:**
```
caldav.lib.errors.DAVError: Unexpected response (401)
```

**Causes:**
- Wrong email or password
- Password is account password, not app-specific password
- iCloud 2FA not set up or password not generated correctly

**Fix:**
1. Verify you generated an app-specific password (not account password)
2. Double-check email spelling
3. Test locally (outside container) with Python `caldav` to isolate the issue:
   ```python
   from caldav import DAVClient
   client = DAVClient(
       url="https://caldav.icloud.com/",
       username="your@icloud.com",
       password="xxxx-xxxx-xxxx-xxxx"
   )
   principal = client.principal()
   print(principal)  # If this succeeds, auth works
   ```

### Issue 3: Calendar Path Not Found (404)

**Error:**
```
caldav.lib.errors.NotFoundError: Unexpected response (404) for path /caldav/v2/...
```

**Cause:** Calendar ID is wrong or calendar doesn't exist.

**Fix:**
1. Run the discovery script (Appendix A, Step 2) to find correct path
2. Verify the calendar exists in iCloud web UI (icloud.com/calendar)
3. Check for typos in email address

### Issue 4: Intermittent Timeouts or "Connection Reset"

**Error:**
```
requests.exceptions.ConnectTimeout: Connection timeout
requests.exceptions.ChunkedEncodingError: Connection broken
```

**Cause:** iCloud CalDAV flaky; connection pooling not working.

**Fix:**
1. Implement exponential backoff in container (already in NFR-2)
2. Set appropriate timeouts: read 30s, connect 10s
3. Use connection pooling with keep-alive
4. Check iCloud status at https://www.icloud.com/system-status/
5. If persistent, may need to add retry delay in `caldav` client configuration

### Issue 5: Rate Limiting (403 Forbidden or Rate Limit Headers)

**Error:**
```
Unexpected response (403) for path /caldav/v2/...
X-RateLimit-Remaining: 0
```

**Cause:** Exceeded iCloud's ~1000 requests/hour limit.

**Fix:**
1. Implement request queuing in container (defer non-urgent syncs)
2. Cache event lists locally (don't query every second)
3. Increase time between queries (batch them)
4. Monitor `caldav_rate_limit_remaining` metric; alert when low

### Issue 6: Container Exits on Startup

**Cause:** CalDAV connection fails during startup health check.

**Fix:**
1. Check container logs: `docker-compose logs caldav-gateway`
2. Verify `CALDAV_SOURCES` JSON is valid (use JSON linter)
3. Test CalDAV credentials outside container first
4. If credentials are correct but iCloud is down, add retry logic with delayed startup

### Issue 7: Memory/CPU Spinning Under Load

**Cause:** No connection pooling or excessive logging.

**Fix:**
1. Ensure connection pooling is configured (pool size 10 per NFR-2)
2. Reduce log verbosity if excessive debug logging is on
3. Implement request queuing to avoid thundering herd
4. Monitor memory with `docker stats caldav-gateway`

---

## Appendix C: iCloud CalDAV Testing Checklist

- [ ] iCloud app-specific password generated and verified
- [ ] Calendar ID discovered via discovery script
- [ ] `ca-certificates` included in Docker image
- [ ] Container starts without SSL errors
- [ ] `/health` endpoint returns `connected` status for iCloud calendar
- [ ] `GET /api/v1/calendars` returns iCloud calendar metadata
- [ ] `GET /api/v1/calendars/icloud/events` returns events (test with date range)
- [ ] **`GET /api/v1/calendars/icloud/today` returns today's events + analysis** ← Daily brief
  - [ ] Free slots correctly computed (gaps > 15 minutes)
  - [ ] Attendee aggregation accurate
  - [ ] Times in user's local timezone
  - [ ] Response cached (query twice in < 5 min should be identical)
- [ ] Created event in iCloud shows up in REST response within 5 seconds
- [ ] Updated event via REST propagates to iCloud
- [ ] Deleted event via REST no longer appears in REST response
- [ ] Recurring events expand correctly in date range queries
- [ ] Rate-limit headers logged; metric exposed in `/metrics`
- [ ] Container survives iCloud outage (returns 503, doesn't crash)
- [ ] Graceful shutdown on SIGTERM (closes connections within 10s)
- [ ] **Daily brief data persists in cache during brief iCloud outage** ← For resilience

---

## References

- [RFC 4791: CalDAV](https://tools.ietf.org/html/rfc4791)
- [RFC 5545: iCalendar](https://tools.ietf.org/html/rfc5545)
- [caldav Python library](https://github.com/collective/caldav)
- [FastAPI docs](https://fastapi.tiangolo.com/)
- [Prometheus monitoring](https://prometheus.io/)
