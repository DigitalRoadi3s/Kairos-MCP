#!/usr/bin/env bash
# Run from inside caldav-gateway after `gh auth login`.
set -e

gh label create sonnet --color 1D76DB --force
gh label create opus --color 5319E7 --force
gh label create haiku --color 0E8A16 --force
gh label create phase-1 --color FBCA04 --force

gh issue create --title "task 2 (sonnet): config manager" \
  --body "FR-1. Load CALDAV_SOURCES env JSON or config.yaml, validate connectivity on startup, fail gracefully if unreachable. Depends on: task 1." \
  --label sonnet,phase-1

gh issue create --title "task 4 (sonnet): GET /calendars, GET /calendars/{id}/events" \
  --body "FR-2. Date-range filtering, title/attendee filters, 400/404/401/503 errors. Depends on: task 3 (done)." \
  --label sonnet,phase-1

gh issue create --title "task 5 (opus): /today free-slot engine" \
  --body "FR-6. Free-slot computation (gaps > 15min), attendee aggregation, 5-min cache with invalidation-safe design. Flagged High risk (cache invalidation race). Depends on: task 3 (done)." \
  --label opus,phase-1

gh issue create --title "task 6 (sonnet): wire /today endpoint" \
  --body "Wire /today endpoint handler to task 5's module; timezone query param handling. Depends on: task 5." \
  --label sonnet,phase-1

gh issue create --title "task 7 (haiku): /health and /metrics stub endpoints" \
  --body "FR-7. prometheus_client wiring, basic gauges. Depends on: task 1 (done)." \
  --label haiku,phase-1

gh issue create --title "task 8 (haiku): README setup + daily brief example" \
  --body "Appendix A Steps 5-8. Depends on: task 4, task 6." \
  --label haiku,phase-1

gh issue create --title "task 9 (haiku): unit test scaffolding for iCal parsing" \
  --body "pytest fixtures/harness only, no assertions for hard edge cases. Depends on: task 3 (done)." \
  --label haiku,phase-1

echo "Phase 1 issues created."
