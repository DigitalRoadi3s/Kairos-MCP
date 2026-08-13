#!/usr/bin/env bash
# Run from inside caldav-gateway after `gh auth login`.
set -e

gh label create phase-2 --color C5DEF5 --force

gh issue create --title "task 10 (opus): iCalendar generation (write-back)" \
  --body "Builds VEVENT for create/update. Fixed a real bug: single-attendee ATTENDEE parsing. Depends on: task 3 (done)." \
  --label opus,phase-2

gh issue create --title "task 11 (sonnet): POST create endpoint" \
  --body "FR-3. Validation, wired to task 10. Depends on: task 10 (done)." \
  --label sonnet,phase-2

gh issue create --title "task 12 (sonnet): PUT update endpoint" \
  --body "FR-4. PATCH semantics, 15-min lock window, recurrence-immutable check. Depends on: task 10 (done)." \
  --label sonnet,phase-2

gh issue create --title "task 13 (sonnet): DELETE endpoint" \
  --body "FR-5. 15-min lock window. Depends on: task 3 (done)." \
  --label sonnet,phase-2

gh issue create --title "task 14 (opus): cache invalidation wiring" \
  --body "Invalidate /today cache AFTER successful write, never before, per PRD's cache-invalidation-race mitigation. Depends on: task 5, 11, 12, 13 (all done)." \
  --label opus,phase-2

gh issue create --title "task 15 (haiku): integration test scaffolding for write lifecycle" \
  --body "create/update/delete against mocked backend. 5 passing, 3 skipped placeholders for task 22. Depends on: task 11, 12, 13 (done)." \
  --label haiku,phase-2

echo "Phase 2 issues created."
