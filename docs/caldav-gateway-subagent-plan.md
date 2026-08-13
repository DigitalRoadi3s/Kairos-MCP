# CalDAV REST Gateway — Sub-Agent Task Plan

Source: `caldav-rest-connector-prd.md`. Scope: all 4 phases. For dispatch via Claude Code's Task tool in a single orchestrating session.

## Model assignment rule

- **Opus** — anything where a subtle bug is hard to catch later: timezone/VTIMEZONE math, free-slot gap computation, cache-invalidation ordering, circuit-breaker state machine, iCalendar generation correctness, RRULE expansion. These are the items the PRD's own risk table flags High/Medium severity.
- **Sonnet** — endpoint wiring, request validation, CRUD logic, test coverage. Well-specified in the PRD, moderate complexity, low ambiguity once Opus's interfaces exist.
- **Haiku** — mechanical, low-ambiguity output: Dockerfile, requirements.txt, README, config/logging/metrics boilerplate, test scaffolding shells, docker-compose.

Each task below lists: model, PRD section it implements, dependencies, and what to hand the subagent.

---

## Phase 1: MVP

| # | Task | Model | Depends on |
|---|------|-------|-----------|
| 1 | Repo scaffold: directory layout, `requirements.txt`, Dockerfile (Appendix A Step 3), `.dockerignore`, FastAPI app skeleton with stub `/health` | Haiku | — |
| 2 | FR-1 Config Manager: load `CALDAV_SOURCES` env JSON or `config.yaml`, validate CalDAV connectivity on startup, fail gracefully if unreachable | Sonnet | 1 |
| 3 | **CalDAV client + iCal transformation core**: connection-pooled client (PROPFIND/REPORT), iCalendar→JSON transform, VTIMEZONE stripping/UTC normalization (Gotcha #6), recurring-event expansion within a date range. Define the interfaces Sonnet tasks 4/6 build against. | Opus | 1 |
| 4 | FR-2 `GET /calendars`, `GET /calendars/{id}/events`: date-range filtering, title/attendee filters, 400/404/401/503 errors | Sonnet | 3 |
| 5 | **FR-6 `/today` engine**: free-slot computation (gaps > 15 min), attendee aggregation, 5-min cache with invalidation-safe design (flagged High risk: cache invalidation race) | Opus | 3 |
| 6 | Wire `/today` endpoint handler to task 5's module; `timezone` query param handling | Sonnet | 5 |
| 7 | FR-7 `/health` and `/metrics` stub endpoints (prometheus_client wiring, basic gauges) | Haiku | 1 |
| 8 | README: setup instructions + daily brief example (Appendix A Steps 5–8) | Haiku | 4, 6 |
| 9 | Unit test scaffolding for iCal parsing edge cases (pytest fixtures/harness only — Opus's task 3 output defines what needs testing) | Haiku | 3 |

Parallelizable: 2 and 3 can run concurrently after 1.

---

## Phase 2: Write Support

| # | Task | Model | Depends on |
|---|------|-------|-----------|
| 10 | **iCalendar generation (write-back)**: build valid VEVENT objects from request JSON, all-day vs timed handling, timezone attachment rules | Opus | 3 |
| 11 | FR-3 `POST` create endpoint: validation (title required ≤200 chars, `start_time < end_time`, all-day handling), wired to task 10, 400/409/401/503 | Sonnet | 10 |
| 12 | FR-4 `PUT` update endpoint: partial-update semantics, 15-min pre-start lock, recurrence-immutable constraint | Sonnet | 10 |
| 13 | FR-5 `DELETE` endpoint: 15-min lock constraint, response shape | Sonnet | 3 |
| 14 | **Cache invalidation wiring**: create/update/delete must invalidate `/today` and `/events` caches without a race against in-flight reads (ties to task 5's design) | Opus | 5, 11, 12, 13 |
| 15 | Integration test scaffolding: create→read→update→delete lifecycle skeleton (pytest-asyncio, httpx) | Haiku | 11, 12, 13 |

---

## Phase 3: Polish & Ops

| # | Task | Model | Depends on |
|---|------|-------|-----------|
| 16 | **Circuit breaker + retry design**: exponential backoff (2s–30s), open circuit after 5 consecutive failures in 60s → 503 for 5 min. Concurrency-sensitive. | Opus | 3 |
| 17 | Wire circuit breaker into all CalDAV client call sites | Sonnet | 16 |
| 18 | Structured JSON logging (structlog config), redact passwords/tokens per NFR-4 | Haiku | 1 |
| 19 | Prometheus metrics: `caldav_request_duration_seconds`, `caldav_calendar_sync_errors_total`, `api_request_duration_seconds`, `api_errors_total`, `caldav_rate_limit_remaining` | Haiku | 7 |
| 20 | OpenAPI schema polish: descriptions/examples on Pydantic models | Haiku | 4, 11, 12, 13 |
| 21 | `docker-compose.yml` + env file example (Appendix A Step 4) | Haiku | 1 |
| 22 | Full unit + integration coverage pass: fill scaffolds from 9/15, add rate-limit-403 handling, SSL cert-failure test, timezone tests (America/New_York, Europe/London, Asia/Tokyo per PRD) | Sonnet | 9, 15, 17 |
| 23 | Graceful shutdown: trap SIGTERM, close CalDAV connections, exit within 10s | Haiku | 3 |

---

## Phase 4: Advanced (backlog — hold until Phase 3 ships)

| # | Task | Model | Depends on |
|---|------|-------|-----------|
| 24 | Multi-source support: extend config manager (task 2) for Google Calendar (CalDAV) and Nextcloud sources | Sonnet | 2, 22 |
| 25 | Recurring-event write support (currently read-only expand-only): full RRULE generation/editing if promoted into scope | Opus | 10, 22 |
| 26 | Unscoped backlog: attendee management, calendar color/category metadata, web UI — do not dispatch, no spec yet | — | — |

---

## Dependency graph (critical path)

```
1 → 3 → {4, 5} → 6
      → 10 → {11, 12} → 14 → 22
   3 → 13 ────────────┘
   3 → 16 → 17 ────────┘
1 → 2 (parallel, feeds task 24 later)
```

Everything in the Haiku column (7, 8, 9, 15, 18, 19, 20, 21, 23) is a leaf off its Sonnet/Opus dependency and can be dispatched as soon as that dependency lands — no need to serialize Haiku tasks behind each other.

## Dispatching a task

For each row, the Task-tool prompt should include:
1. The task's PRD section(s) verbatim (FR-#, NFR-#, or the named Gotcha/Risk).
2. The exact interface/contract produced by its dependency task(s) — don't let a Sonnet task re-derive what an Opus task already decided.
3. Expected output files.
4. The relevant line(s) from **Success Criteria (Phase 1 MVP)** or **Appendix C Testing Checklist** as acceptance criteria.

Want me to also generate the actual `.claude/agents/*.md` subagent definition files (with `model:` frontmatter set to opus/sonnet/haiku) so these can be invoked directly, or keep this as the planning doc and hand-write Task-tool calls as you go?
