// import { useEffect } from "react";
// import { useLocation } from "react-router-dom";
// import { api } from "../api";
// import { Card, ErrorBox, Loading, PolicyDetails, humanText, postureLabel, ruleLabel, useAsync } from "../components/ui";

// function conditionText(condition: string | {any: Record<string, unknown>[]}) {
//   if (typeof condition === "string") return humanText(condition);
//   return condition.any.map(c => c.type === "forecast_gust" ? `Forecast gusts at least ${c.min_mph} mph${c.below_mph ? `, below ${c.below_mph} mph` : ""} during the commute`
//     : c.type === "forecast_wind" ? `Sustained wind at least ${c.min_mph} mph for ${c.duration_minutes} minutes during the commute`
//     : `${Array.isArray(c.events) ? c.events.join(" or ") : `Alert severity ${Array.isArray(c.severity) ? c.severity.join(" / ") : "any"}; urgency ${Array.isArray(c.urgency) ? c.urgency.join(" / ") : "any"}`} during ${String(c.window ?? "operating day").replaceAll("_", " ")}`).join("; or ");
// }

// export default function ThresholdsPage() {
//   const policy = useAsync(() => api.thresholds(), []);
//   const location = useLocation();
//   useEffect(() => {
//     if (location.hash && policy.data) document.getElementById(location.hash.slice(1))?.scrollIntoView();
//   }, [location.hash, policy.data]);
//   if (policy.loading) return <Loading />;
//   if (policy.error) return <ErrorBox error={policy.error} />;
//   if (!policy.data) return null;
//   return <div className="space-y-5">
//     <h1 className="text-2xl font-bold">Thresholds</h1>
//     <PolicyDetails version={policy.data.version} />
//     <p>Sources define hazards and employment obligations. Every hazard-to-posture mapping is a choice of Facility policy v1.0 (this system), subject to manager approval.</p>
//     <Card title="Change log">{policy.data.change_log.map(c => <p key={c.version}>{c.date} · {c.version}: {c.changes}</p>)}</Card>
//     {([['federal', 'Federal standards and source definitions (NWS / NOAA / OPM / CAP)'], ['coordination', 'Local coordination (FEB)'], ['facility', 'Facility policy choices — not federal standards']] as const).map(([group, label]) =>
//       <Card key={group} title={label}><div className="overflow-x-auto"><table className="w-full text-sm">
//         <thead><tr className="text-left">{['Rule', 'Condition', 'Posture', 'Source / section', 'Verified', 'Justification', 'Last verified'].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
//         <tbody>{policy.data!.rules.filter(r => r.group === group).map(r => <tr id={r.id} key={r.id} className="scroll-mt-4 border-t border-slate-200 align-top target:bg-yellow-50">
//           <td className="p-2"><strong>{ruleLabel(r.id)}</strong><p>{humanText(r.description)}</p><details><summary>Technical details</summary><code>{r.id}</code></details></td>
//           <td className="p-2">{conditionText(r.condition as string | {any: Record<string, unknown>[]})}</td>
//           <td className="p-2">{r.posture in {normal: 1, closure: 1, delayed_arrival: 1, early_dismissal: 1, telework: 1} ? postureLabel(r.posture as Parameters<typeof postureLabel>[0]) : humanText(r.posture)}</td>
//           <td className="p-2"><a className="underline" href={r.citation.url} target={r.citation.url.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{r.citation.title}</a><p>{r.citation.publisher}</p><p>{r.citation.section_or_page}</p></td>
//           <td className="p-2"><span className={`rounded px-2 py-1 ${r.verified ? 'bg-green-100 text-green-900' : 'bg-amber-100 text-amber-900'}`}>{r.verified ? 'Verified' : 'Unverified'}</span></td>
//           <td className="p-2">{r.justification}</td><td className="p-2">{r.verified ? r.citation.retrieved_at : 'Not verified'}</td>
//         </tr>)}</tbody>
//       </table></div></Card>)}
//   </div>;
// }
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import {
  Card,
  ErrorBox,
  Loading,
  PolicyDetails,
  humanText,
  postureLabel,
  ruleLabel,
  useAsync,
} from "../components/ui";

const GROUPS = [
  ["federal", "Official hazard references and facility responses"],
  ["coordination", "Agency coordination references"],
  ["facility", "Facility policy choices — not federal standards"],
] as const;

const POSTURES = new Set([
  "normal",
  "closure",
  "delayed_arrival",
  "early_dismissal",
  "telework",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

function valueText(value: unknown): string {
  if (value == null) return "not specified";

  if (Array.isArray(value)) {
    return value.map(valueText).join(", ");
  }

  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, item]) => `${humanText(key)}: ${valueText(item)}`)
      .join("; ");
  }

  return String(value);
}

function windowText(value: unknown, fallback: string): string {
  return typeof value === "string"
    ? humanText(value)
    : fallback;
}

function conditionText(condition: unknown): string {
  if (condition == null) return "Not specified";

  if (typeof condition === "string") {
    return humanText(condition);
  }

  if (Array.isArray(condition)) {
    return condition.map(conditionText).join("; ");
  }

  if (!isRecord(condition)) {
    return valueText(condition);
  }

  // Preserve AND/OR grouping and any additional conditions.
  if (Array.isArray(condition.any) || Array.isArray(condition.all)) {
    return Object.entries(condition)
      .map(([key, value]) => {
        if ((key === "any" || key === "all") && Array.isArray(value)) {
          if (value.length === 0) return `${key}: no conditions listed`;

          const separator = key === "any" ? " OR " : " AND ";

          return value
            .map(item => `(${conditionText(item)})`)
            .join(separator);
        }

        return `${humanText(key)}: ${valueText(value)}`;
      })
      .join("; ");
  }

  const c = condition;

  if (c.type === "forecast_gust" || c.type === "forecast_wind") {
    const bounds: string[] = [];

    if (c.min_mph != null) {
      bounds.push(`at least ${valueText(c.min_mph)} mph`);
    }

    if (c.below_mph != null) {
      bounds.push(`below ${valueText(c.below_mph)} mph`);
    }

    const subject =
      c.type === "forecast_gust"
        ? "Forecast gusts"
        : "Sustained wind";

    const duration =
      c.duration_minutes != null
        ? ` for ${valueText(c.duration_minutes)} minutes`
        : "";

    const window = windowText(c.window, "the commute");

    const knownKeys = new Set([
      "type",
      "min_mph",
      "below_mph",
      "duration_minutes",
      "window",
    ]);

    // Keep unfamiliar fields visible rather than silently dropping them.
    const extraConditions = Object.entries(c)
      .filter(([key]) => !knownKeys.has(key))
      .map(([key, value]) => `${humanText(key)}: ${valueText(value)}`);

    const summary =
      `${subject} ${bounds.length ? bounds.join(", ") : "(speed not specified)"}` +
      `${duration} during ${window}`;

    return [summary, ...extraConditions].join("; ");
  }

  // Generic rendering retains fields such as certainty, geography
  // and timing instead of assuming every other condition is an alert.
  return Object.entries(c)
    .map(([key, value]) => {
      if (key === "events" && Array.isArray(value)) {
        return `Alert: ${value.map(valueText).join(" or ")}`;
      }

      if (key === "window") {
        return `During ${windowText(value, "the specified window")}`;
      }

      return `${humanText(key)}: ${valueText(value)}`;
    })
    .join("; ");
}

function responseText(posture: string): string {
  return POSTURES.has(posture)
    ? postureLabel(posture as Parameters<typeof postureLabel>[0])
    : humanText(posture);
}

function formatDate(value?: string | null): string {
  if (!value) return "Not recorded";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export default function ThresholdsPage() {
  const policy = useAsync(() => api.thresholds(), []);
  const location = useLocation();
  const data = policy.data;

  useEffect(() => {
    if (!location.hash || !data) return;

    let id = location.hash.slice(1);

    try {
      id = decodeURIComponent(id);
    } catch {
      // Use the original fragment if URI decoding fails.
    }

    document.getElementById(id)?.scrollIntoView({
      block: "start",
    });
  }, [location.hash, data]);

  if (policy.loading) return <Loading />;
  if (policy.error) return <ErrorBox error={policy.error} />;
  if (!data) return null;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Thresholds</h1>

      <PolicyDetails version={data.version} />

      <div className="space-y-2">
        <p>
          Official sources describe hazards and operating procedures.
          Every hazard-to-posture mapping is a choice of Facility
          policy {data.version}, subject to manager approval.
        </p>

        <p className="text-sm text-slate-600">
          Source review labels describe whether a reference has been
          checked. They do not establish agency approval of the
          facility response or validation of its implementation.
          Retrieval dates show when references were obtained.
        </p>
      </div>

      <Card title="Change log">
        {data.change_log.length > 0 ? (
          <ul className="space-y-2">
            {data.change_log.map(change => (
              <li key={`${change.version}-${change.date}`}>
                <strong>{change.version}</strong>
                {" · "}
                {change.date}: {change.changes}
              </li>
            ))}
          </ul>
        ) : (
          <p>No changes recorded.</p>
        )}
      </Card>

      {GROUPS.map(([group, label]) => {
        const rules = data.rules.filter(rule => rule.group === group);

        if (rules.length === 0) return null;

        return (
          <Card key={group} title={label}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  {label}
                </caption>

                <thead>
                  <tr className="text-left">
                    {[
                      "Rule",
                      "Condition",
                      "Response / reference role",
                      "Source / section",
                      "Source review",
                      "Justification",
                      "Reference retrieved",
                    ].map(heading => (
                      <th
                        scope="col"
                        className="p-2"
                        key={heading}
                      >
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {rules.map(rule => {
                    const externalLink = /^https?:\/\//i.test(
                      rule.citation.url
                    );

                    return (
                      <tr
                        id={rule.id}
                        key={rule.id}
                        className="scroll-mt-4 border-t border-slate-200
                                   align-top target:bg-yellow-50"
                      >
                        <th
                          scope="row"
                          className="p-2 text-left font-normal"
                        >
                          <strong>{ruleLabel(rule.id)}</strong>

                          <p>{humanText(rule.description)}</p>

                          <details className="mt-2">
                            <summary className="cursor-pointer">
                              Technical details
                            </summary>

                            <code className="break-all">
                              {rule.id}
                            </code>

                            <pre
                              className="mt-2 max-w-sm whitespace-pre-wrap
                                         break-words rounded bg-slate-50
                                         p-2 text-xs"
                            >
                              {JSON.stringify(rule.condition, null, 2)}
                            </pre>
                          </details>
                        </th>

                        <td className="p-2">
                          {conditionText(rule.condition)}
                        </td>

                        <td className="p-2">
                          {responseText(rule.posture)}
                        </td>

                        <td className="p-2">
                          <a
                            className="underline"
                            href={rule.citation.url}
                            target={externalLink ? "_blank" : undefined}
                            rel={
                              externalLink
                                ? "noopener noreferrer"
                                : undefined
                            }
                          >
                            {rule.citation.title}
                          </a>

                          <p>{rule.citation.publisher}</p>
                          <p>{rule.citation.section_or_page}</p>
                        </td>

                        <td className="p-2">
                          <span
                            className={`inline-block rounded px-2 py-1 ${
                              rule.verified
                                ? "bg-green-100 text-green-900"
                                : "bg-amber-100 text-amber-900"
                            }`}
                          >
                            {rule.verified
                              ? "Source checked"
                              : "Review pending"}
                          </span>
                        </td>

                        <td className="p-2">
                          {rule.justification}
                        </td>

                        <td
                          className="p-2"
                          title={rule.citation.retrieved_at || undefined}
                        >
                          {formatDate(rule.citation.retrieved_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
