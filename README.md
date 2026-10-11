# Closure Call
### Make the call. Reach the right people. Keep the reasons.

🌐 **[Try the live app](https://closurecall.duckdns.org/team)**
### Make the call. Reach the right people. Keep the reasons.

It’s 4 a.m. Severe weather is approaching, and employees will soon leave home. A facility manager must decide: **open normally, delay, dismiss early, offer telework or close?**

The answer is scattered across weather alerts, road reports and employee schedules. Closure Call brings that evidence together so the manager can make an informed decision and communicate it before the commute begins.

> **Agents investigate. Code applies reviewed rules. A human makes the final call.**

## How it works

![Closure Call architecture](closure-call-architecture.png)

*Solid arrows show the workflow. Dashed arrows show information saved to the decision record.*

### 1. Understand the situation

Collect live NWS weather data, river conditions and available local reports. Connect that evidence to the facility’s location, operating hours and employee schedules.

### 2. Recommend and explain

Code applies reviewed thresholds to recommend an operating status. The manager sees the supporting evidence, source timestamps, missing information and what would change the recommendation.

Official hazard definitions inform the thresholds; facility policy determines the proposed response.
The Thresholds page lists policy 1.0.0's verified citations and explains which responses are
facility choices rather than federal standards.

### 3. Ask for more evidence

Not convinced? The manager can ask:

- “Is the access road still closed?”
- “What is the river forecast for the morning?”
- “Why recommend a delay instead of closure?”

Strands agents investigate approved sources or explain the saved evidence. New findings produce a revised recommendation for the manager to review.
Checks always concern the recommendation's target day. Saved-record explanations and one-tap
checks work without inference. Managers can also schedule a recheck or ask facility operations
staff for site evidence; human reports are labeled separately and cannot lower the posture.

### 4. Decide and notify

The manager approves or overrides the recommendation with a reason. Code then rechecks assignments, shifts, leave and essential roles before preparing the appropriate notifications.

An onsite employee may need a new reporting time. Someone on leave may need no routine alert. Essential staff receive instructions specific to their responsibilities.

### 5. Keep the decision record

Save what was known, which rules applied, what the manager decided and the notification outcomes—so the full decision can be reconstructed later.

## Evidence behind the decision

| Input | What it adds |
| --- | --- |
| **NWS alerts and forecasts** | Weather hazards and timing |
| **NOAA river gauges** | River levels and relevant flood categories |
| **Official road reports** | Facility access and commute disruptions |
| **Schools, public safety and local news** | Supporting context and leads to verify |
| **Synthetic facility and employee records** | Who is affected, when and how |

News alone does not trigger closure. Missing data is marked unknown. Recipient selection follows explicit rules, and operational notifications require manager approval.

## Built for Hack Midwest

| Challenge | Role in Closure Call |
| --- | --- |
| **USDA** | Facility decisions, targeted notifications and a clear decision record |
| **AWS Strands Agents SDK** | Agents that gather evidence and investigate manager questions |
| **Akamai Cloud** | Cloud hosting for the application and supporting services |

## The demo journey

**Live evidence → explained recommendation → manager follow-up → approval → targeted notification → decision record.**

*Hackathon prototype using synthetic employee data. Available integrations depend on the deployed build. Recorded and simulated scenarios are labeled separately from live evidence. Facility response rules are prototype policies, not official USDA directives.*

## Resources

Technical details: [architecture](docs/architecture.md) (pipeline, agents, caching, rate limits, scheduled re-evaluations) · [sources](docs/sources.md)


[Strands SDK](https://github.com/strands-agents/sdk-python) ·
[Akamai starter kit](https://github.com/akamai-developers/akamai-workshop-hackmidwest) ·
[NWS API](https://www.weather.gov/documentation/services-web-api) ·
[NOAA water API](https://water.noaa.gov/about/api)
