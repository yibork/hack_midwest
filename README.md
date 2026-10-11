# Closure Call
### Make the call. Reach the right people. Keep the reasons.

🌐 **[Try the live app](https://closurecall.duckdns.org/team)**

It’s 4 a.m. Severe weather is approaching, and employees will soon leave home. A facility manager must decide: **open normally, delay, dismiss early, offer telework or close?**

The answer is scattered across weather alerts, road reports and employee schedules. Closure Call brings that evidence together so the manager can make an informed decision and communicate it before the commute begins.

> **Agents investigate. Code applies reviewed rules. A human makes the final call.**

## How it works

![Closure Call decision workflow](closure-call-architecture.png)

*Solid arrows show the workflow. Dashed arrows show information saved to the decision record.*

1. **Understand the situation.** Gather weather, river and local conditions alongside facility details and employee schedules.
2. **Recommend and explain.** Apply reviewed rules and show supporting evidence, missing information and what would change the recommendation.
3. **Investigate further.** Let the manager request additional evidence before deciding.
4. **Decide and notify.** Record the manager’s decision, check who is affected and send appropriate instructions.
5. **Keep the history.** Preserve evidence, reasoning, decisions and notification outcomes.

An onsite employee may need a new reporting time. Someone on leave may need no routine alert. Essential staff receive instructions specific to their responsibilities.

## How our AI agents help

![Strands agents connected to Akamai AI inference, with rules-based recommendations and human approval](closure-call-strands-agents.png)

**Strands provides the agent framework. Akamai provides the AI inference.**

The weather agent uses Python tools to retrieve evidence. A local coordinator selects specialists to analyze roads, schools, public-safety reports and news. Writer and message agents turn validated facts into clear explanations and notification wording.

The manager can ask **“Is the access road still closed?”** or **“Why recommend a delay?”** The investigator checks approved sources or explains saved evidence. New findings produce a revised recommendation for review.

Python code validates outputs, applies decision rules and selects recipients. Agents operate within call budgets and timeouts, with activity recorded for traceability. The manager retains final approval.

## Evidence behind the decision

| Input | What it adds |
| --- | --- |
| **NWS alerts and forecasts** | Weather hazards and timing |
| **NOAA river gauges** | River levels and relevant flood categories |
| **Official road reports** | Facility access and commute disruptions |
| **Schools, public safety and local news** | Supporting context and leads to verify |
| **Synthetic facility and employee records** | Who is affected, when and how |

Official hazard definitions inform the thresholds; facility policy determines the proposed response. News alone does not trigger closure, and missing data is marked unknown.

## CI/CD and cloud deployment

![Closure Call CI/CD pipeline, cloud hosting and database connections](closure-call-cicd-cloud.png)

Our **CI/CD pipeline** connects development to the deployed application: push code to GitHub, run automated checks, then release successful updates to Akamai Cloud when auto-deployment is enabled.

| Part | What it does |
| --- | --- |
| **CI — Continuous Integration** | GitHub Actions runs backend and frontend tests and builds the frontend |
| **CD — Continuous Deployment** | Successful CI on main triggers deployment through SSH and a Docker rebuild |
| **Database** | SQLAlchemy reads and saves facilities, employees, evidence, decisions and notification records |
| **External services** | Supply weather data, Akamai AI inference, ntfy push notifications and Resend email |

The application uses configured PostgreSQL, with SQLite as a fallback. Raw evidence snapshots are stored separately on persistent disk.

*Steps 1–3 in the figure describe releasing updates. Steps 4–5 describe everyday use.*

## Built for Hack Midwest

| Challenge | Our contribution |
| --- | --- |
| **USDA** | Facility decisions, targeted notifications and a reconstructable decision record |
| **AWS Strands Agents SDK** | Specialized agents with tools, validated outputs and manager-led investigation |
| **Akamai Cloud** | Application hosting and AI inference |

## The demo journey

**Live evidence → recommendation → manager question → approval → targeted notification → decision record.**

*Hackathon prototype using synthetic employee data. Available integrations depend on the deployed build. Recorded and simulated scenarios are labeled separately from live evidence. Facility response rules are prototype policies, not official USDA directives.*

## Resources

[Strands SDK](https://github.com/strands-agents/sdk-python) ·
[Akamai starter kit](https://github.com/akamai-developers/akamai-workshop-hackmidwest) ·
[NWS API](https://www.weather.gov/documentation/services-web-api) ·
[NOAA water API](https://water.noaa.gov/about/api)