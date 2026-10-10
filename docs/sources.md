# Local signal sources

The local agents (roads, schools, public safety, news) read **only** the URLs in
[`backend/app/sources/registry.yaml`](../backend/app/sources/registry.yaml). This page records how
each one was chosen and checked. Research date: 2026-10-10.

## How sources were checked

For each candidate:
1. The URL was requested with our descriptive User-Agent. We confirmed it returns data and noted
   whether it is JSON, RSS or server-rendered HTML.
2. `robots.txt` was parsed for our User-Agent (`ClosureCall`) and for AI agents (`ClaudeBot`,
   `anthropic-ai`, `GPTBot`). The text is read by a language model, so a site that opts out of AI
   agents is skipped even when it allows our User-Agent. The fetcher repeats this check at runtime
   and caches robots.txt for 6 hours. A 401 or 403 on robots.txt counts as "disallow"; a 404 means
   no restrictions.
3. For map-style apps we looked for the JSON API the page itself loads, and used that instead of
   scraping:
   - MoDOT: `timconfig/feed/desktop/message.v2.json`, found in the map's `site.js`
   - KanDrive: Castle Rock CARS `events_v1/api/eventReports`, found in the app bundle
   - Evergy: Kubra StormCenter, whose `currentState` call leads to a county report
4. No logins, no social networks, and we never follow links found inside a page. Redirects are
   followed only within the source's `allow_prefixes`. During research, `kcbeacon.org/feed/` began
   redirecting to `thebeaconnews.org`; the allowlist blocked it until the registry was updated.

## Used

| Source | Agent | Authority | Kind | Notes |
|---|---|---|---|---|
| MoDOT Traveler Information | roads | official_state | JSON | ~1,000 statewide items, filtered to KC-area counties; commuter lots dropped. Text includes "Impact reported at …" (report time), kept apart from when the closure starts. |
| KanDrive (KDOT) | roads | official_state | API (JSON) | Filtered to a KC-metro bounding box. Has `beginTime` (effective) and `updateTime` (report). Staff contact names and phone numbers in the feed are never stored. |
| KC Scout | roads | official_state | HTML | Server-rendered incident list (MoDOT/KDOT program). It lists ramp, exit and lane closures alongside full closures; code only counts a mainline full closure as `road_closure`. |
| Kansas City Public Schools | schools | official_local | HTML (Apptegy) | Date-only posts: today's and yesterday's are kept. |
| North Kansas City Schools | schools | official_local | HTML (Finalsite) | |
| Hickman Mills C-1 | schools | official_local | HTML (Finalsite) | |
| Independence School District | schools | official_local | HTML (Finalsite) | Its RSS endpoint returns 403, so the news page is used. |
| KSHB 41 school closings | schools | news | HTML | Had no active closings on the research date, so the per-entry markup couldn't be seen. The closings region is read as one page-level item, or none when it says there are no closings. |
| KCPD news releases (kcpolice.org) | public_safety | official_local | HTML | No dates on the list page; items are marked undated. |
| Evergy outage map (Kubra) | public_safety | official_local | API (JSON) | Customers out per county; counties under 200 customers out are ignored. Evergy is a private utility, listed as official_local for outage data only. |
| KSHB 41 local news | news | news | RSS | Its robots.txt blocks only GPTBot. |
| KCUR 89.3 news | news | news | RSS | |
| The Beacon (Kansas City) | news | news | RSS | Also covers Wichita. |

Already integrated before this layer: the NWS API (alerts by point, zone and nationally; `/points`
for forecastHourly and forecastGridData). **NOAA NWPS gauge KCDM7** is registered as an Evidence
source type, but no client for it exists in this codebase yet (the API itself answers 200).

## Skipped

| Candidate | Why |
|---|---|
| MoDOT `traveler.modot.org/reportv2` | 404; the map's JSON feed is used instead. |
| KMBC 9 closings and RSS | robots.txt disallows AI agents. |
| FOX4 KC closings and RSS | robots.txt disallows AI agents; the closings page returns 403. |
| KCTV5 closings | robots.txt disallows AI agents. |
| The Kansas City Star | Timed out for automated clients; robots.txt disallows AI agents. |
| City of Kansas City, MO (news, Notify KC) | 403 to automated clients; Notify KC is SMS/email sign-up only. |
| Jackson County, MO emergency management | 403 to automated clients. |
| Unified Government of Wyandotte County/KCK | 403 to automated clients, including robots.txt. |
| KC Fire Department press releases | On kcmo.gov (403). |
| Shawnee Mission School District alerts | Static page, no list of announcements. |
| Social networks | Out of scope by policy. |

## What the facts can change

Rules in `backend/app/rules/local.py` (cited as facility policy v1):
- **Facility access route closed.** A road closure from an official state source, on a facility
  access route, during the commute window, triggers the `access_route_closed` rule:
  - `delayed_arrival` if at least one access route stays open
  - `telework` if every access route is closed

  The rule's margin reads "N of M access routes closed".
- **Employee commute route closed.** An official state closure on an employee's commute route sets
  that person's `ROUTE_CLOSED` exposure code. It doesn't change the facility posture.
- **Schools.** Closures in the facility county, or in 3 or more employee home counties, add a
  corroboration note. They raise confidence one level only when a weather rule already fired.
- **Official local public safety and utilities.** These add a "manager should check" note, and
  corroborate when a weather rule already fired.
- **News.** Adds a "manager should check" note with the link. News never raises confidence and
  never changes the posture.
- **Never downward.** Nothing from local signals can lower the posture.
