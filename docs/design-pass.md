# Task B: operations design pass

Frontend-only; no API, policy, recipient or notification behavior changes. Theme colours follow
[USWDS theme tokens](https://designsystem.digital.gov/design-tokens/color/theme-tokens/) and
[state tokens](https://designsystem.digital.gov/design-tokens/color/state-tokens/). Public Sans
is loaded from Google Fonts with a system-font fallback. No official .gov banner, agency logo,
or claim of government endorsement is added. DEMO/SIMULATED/REPLAY labels remain visible.

`frontend/src/index.css` contains the colour, spacing, type and radius tokens. Light navigation,
outcome-first summaries, neutral forecast confidence, quiet covered staffing, readable evidence
citations, grouped narrative activity, nearest-threshold progress, striped sticky tables and
single-card personal instructions replace the dashboard styling.

The frontend acknowledgment page is `/acknowledge/{notification_id}`: one explicit button uses
the existing ack API. Existing outbound `/ack/{notification_id}` links still use the unchanged
server-rendered page; restyling that response would not be a frontend-only change.

## Before / after

Screenshots use the same production API records, synthetic roster and target day. Before is
deployed Task A; after is the Task B local frontend preview with a read-only production proxy.
No evaluation, decision, notification or model call was made for the captures.

| Page | Before | After |
| --- | --- | --- |
| Facility, 1280 × 720 | [Before](screenshots/task-b/facility-before.png) | [After](screenshots/task-b/facility-after.png) |
| Decide, 1280 × 720 | [Before](screenshots/task-b/decide-before.png) | [After](screenshots/task-b/decide-after.png) |
| My status, 390 × 844 | [Before](screenshots/task-b/my-status-before.png) | [After](screenshots/task-b/my-status-after.png) |

Additional checks: [Facility phone](screenshots/task-b/facility-phone-after.png),
[Decide phone](screenshots/task-b/decide-phone-after.png),
[My status desktop](screenshots/task-b/my-status-desktop-after.png).
All after captures have document width equal to the requested viewport (390 or 1280 px),
without horizontal page overflow. Wide tables scroll within their panel.

Reproduce on macOS with `node frontend/scripts/capture-design.mjs <preview-origin> after`.
The script uses a fresh temporary browser profile, only reads visible pages, never inspects
saved browser sessions, and leaves the temporary profile recoverable.
