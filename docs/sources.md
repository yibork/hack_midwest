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
| KC Scout | roads | official_state | API (JSON) | `POST DataProvider.asmx/LoadEntities`, the JSON the KC Scout map loads; it has coordinates, unlike the IncidentViewer.aspx page this replaced. It lists ramp, exit and lane closures alongside full closures; code only counts a mainline full closure as `road_closure`. |
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

## Where coordinates come from

Inspected 2026-10-11. Each source's `coordinates` block in `registry.yaml` records the same.

| Source | Fields | Geometry | Coordinate system |
|---|---|---|---|
| MoDOT `message.v2.json` | `GEOM.x` (lon), `GEOM.y` (lat) | point per message | WGS84 (`GEOM.spatialReference.wkid` 4326) |
| MoDOT `LinesV1.json` | `GEOM.paths` | road polylines | WGS84. Not used: the records carry only MT/MST/LOI/GEOM, with no id to join them to messages |
| KanDrive `events_v1/api/eventMapFeatures` | `geometry` (GeoJSON), joined to `eventReports` on `id` | MultiLineString / LineString along the road; Point / MultiPoint for spot events (all 214 events joinable) | WGS84 GeoJSON `[lon, lat]` |
| KanDrive `eventReports` (fallback) | `location.primaryPoint` / `secondaryPoint` `{lat, lon, linearReference}`, `bounds` | segment endpoints (162 of 214 have distinct ends) | WGS84 |
| KC Scout `DataProvider.asmx/LoadEntities` | `Incident[].Latitude`, `Longitude` | point | WGS84 (its map transforms EPSG:4326 -> 3857 for display) |
| School districts, KSHB closings, KCPD, news RSS | none | none | KCPS's page embeds calendar JSON with `latitude`/`longitude` fields that are always empty |
| Evergy (Kubra) | county names only | none | county-level counts |

`kandrive.gov/list/events` is a single-page app shell; its data is the `events_v1` API above.

Every fact carries three location fields:
- `geometry`: GeoJSON.
- `location_provenance`: `source_coordinates`, `geocoded_text` or `none`.
- `location_note`: where the location came from.

Without source coordinates, a fact is geocoded only if its text names a route and a cross street.
The geocoder is OpenStreetMap Nominatim, used within its usage policy: descriptive User-Agent, at
most 1 request per second, and every result (including "not found") cached in `geocode_cache`.
The query is recorded in the note. Geocoded points are approximate: they can mark commute
relevance but never trigger `access_route_closed`. `none` means relevance is `uncertain`, and the
fact never triggers a rule or ROUTE_CLOSED.

## Location relevance

All three road sources give coordinates (MoDOT `GEOM`, KanDrive primary/secondary points, KC
Scout `Latitude`/`Longitude`). Matching is geographic (`backend/app/geo.py`):
- **Facility:** an item matches if a point lies within an access segment's buffer (default
  2 km) on that segment's route. Segments are in the facility's `access_routes`, drawn from
  KC Scout camera locations along I-35, I-70 and US-71 downtown.
- **Employee:** an item matches if it lies within the person's commute corridor (a straight
  line from home to the facility, buffered 3 km) on one of their commute routes.
- **No coordinates:** relevance is `uncertain`. The item is shown, but never triggers a rule or
  ROUTE_CLOSED.
- **Ramp closures** are `road_condition`, unless they are inside a segment marked `ramps: true`.

## What the facts can change

### EAX forecaster products (verified 2026-10-11 UTC)

The deterministic evidence sweep uses the official NWS API:
[AFD index](https://api.weather.gov/products/types/AFD/locations/EAX) and
[HWO index](https://api.weather.gov/products/types/HWO/locations/EAX), then
`https://api.weather.gov/products/{id}` for the latest `issuanceTime` in `@graph`.
Both index/detail sequences returned HTTP 200 with `issuingOffice: KEAX`, matching
`productCode` and nonempty `productText` during verification. Latest sampled IDs were
`9335b416-482c-4079-bded-341c211c0f3d` (AFD, 2026-10-10 23:25 UTC) and
`91016f68-80b2-4a89-9731-77e566001ce2` (HWO, 2026-10-10 10:01 UTC).

Only these fixed endpoints and validated product IDs are fetched, with no redirects or
source-supplied URLs. Raw detail bytes are saved by SHA-256 as official-federal evidence;
products older than 48 hours are stale. Refreshes bypass the usual NWS/local feed caches.
All allowlisted local sources are checked, including news even when the coordinator would
normally skip it. Unchanged parsed items reuse validated facts; changed/new relevant items
alone go to specialists, subject to the shared investigation budget and road-facts cache.
One optional model call extracts target-day forecaster context. Its quote must be an exact
consecutive excerpt of at most 25 words; target-day and hazard words are verified in code.
No model availability or quota is required to fetch, save or re-evaluate evidence. See
Facility policy v1.0 and the Thresholds page's forecaster-context entry for confidence-only
use; text never changes posture. SIMULATED and REPLAY sweeps never fetch live sources.

Rules in `backend/app/rules/local.py` (cited as facility policy v1):
- **Facility access route closed.** A road closure from an official state source, located on a
  facility access segment, during the commute window, triggers the `access_route_closed` rule:
  - `delayed_arrival` if at least one access route stays open
  - `telework` if every access route is closed

  The rule's margin reads "N of M access routes closed".
- **Employee commute route closed.** An official state closure inside an employee's commute
  corridor sets `ROUTE_CLOSED` for that person only. It doesn't change the facility posture.
- **Schools.** Closures in the facility county, or in 3 or more employee home counties, add a
  corroboration note. They raise confidence one level only when a weather rule already fired.
- **Official local public safety and utilities.** These add a "manager should check" note, and
  corroborate when a weather rule already fired.
- **News.** Adds a "manager should check" note with the link. News never raises confidence and
  never changes the posture.
- **Never downward.** Nothing from local signals can lower the posture.
