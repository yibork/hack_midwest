# Facility policy v1.0 (this system)

These are Closure Call prototype choices for a synthetic facility, not USDA directives or
federal hazard standards. A manager authorizes every operating decision and notification.
Source verification confirms a published definition, not federal endorsement of our response.

Air quality (policy 1.0.3): EPA AQI categories are verified; operating mappings are our choices.
Use the highest fresh pollutant AQI from current same-day observations or a forecast for the
target day. AQI 101–150 during operating hours flags sensitive employees without changing
posture; supervisors could offer unscheduled telework to scheduled onsite employees with an
accommodation on file, subject to eligibility/emergency obligations. AQI 151–200 maps to
telework, and AQI 201+ to closure for onsite non-emergency work. AQI above 300 is EPA's Hazardous
category and also meets closure. Missing/stale/non-numeric AQI is unknown, not good. AirNow is
preliminary supplementary data, not regulatory monitoring or an official closure directive.

Weather responses: Extreme/Immediate CAP alerts during work map to closure; EAX High Wind
Warning or equivalent commute wind maps to closure; EAX advisory wind maps to delayed arrival;
flood warnings map to telework; convective warnings map to delayed arrival before opening or
early departure after opening only when safe. Shelter instructions take precedence during an
active threat. Gust forecasts are compared with EAX's bands; one hourly sustained forecast must
cover an entire hour inside the commute window to satisfy the sustained-wind criterion.

Access closures: official source coordinates must match the closure's own route and a facility
access segment (default buffer 2 km). Some routes closed means delay; every route closed means
telework. Commute corridors use a 3 km buffer and affect people individually. Approximate
geocodes and unknown locations never trigger access closure. Ramp/lane restrictions are minor.

Site reports: a facility_ops response identifying unsafe conditions for the target day's commute
window warrants delayed arrival. It remains human-reported, expires after its stated window,
and can never lower an existing posture. The staff-request token is single-use and expires
after two hours. This evidence feature is implemented with the investigation panel.

Staffing: minimums come from each critical function's synthetic roster and posture. Available
staff must be qualified, scheduled, unexposed and expected onsite; call-ins count after ack.

Forecaster context (policy release 1.0.1): an exact, verified EAX AFD/HWO quote explicitly
stating no significant hazards for the saved target day may raise confidence by one level,
only when every required source is current. The existing 24/48-hour lead-time caps still apply.
A possible target-day hazard adds a watch/re-evaluation note instead. Product text, its AI
summary and forecaster confidence never select or change an operating posture. Missing,
stale, fabricated, wrong-day or instruction-like quotes cannot raise confidence. This is a
facility policy choice, not an NWS or federal confidence standard.

Confidence: missing required evidence means low; stale/ambiguous evidence reduces confidence.
Forecast lead time over 24 hours caps at medium, over 48 at low; within 5 mph of a gust threshold
is near-threshold. Local school closures in the facility county or 3 employee home counties
can corroborate a weather rule, raising confidence at most one level. News alone cannot trigger
a posture. These thresholds are our choices, not NWS reliability or staffing standards.

Calendar: default Mon–Fri minus observed federal holidays, with separate critical-function days.
Commute is opening minus 2 hours through opening; departure is closing through closing plus
2 hours. Explicit dates preserve their shifts; simulated workdays must be labelled DEMO.

OPM's December 2025 procedures supply names and employment context. Agency-specific emergency
duties and authorized telework agreements govern actual obligations. Kansas City managers should
confirm their agency's current FEB coordination/contact and status announcements. The opened
OPM FY2019 report documents FEB coordination historically; no current machine-readable FEB feed
was verified. KCDM7 flood stages (29/32/35/49 ft) classify the river, not this facility's safety.
