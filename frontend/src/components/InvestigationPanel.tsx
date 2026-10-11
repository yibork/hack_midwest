import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Investigation, type InvestigationRequest, type RecommendationDetail } from '../api';
import AgentActivity, { toolSentence } from './AgentActivity';
import { Button, Card, ErrorBox, Loading, humanText, ruleLabel, useAsync } from './ui';
import RevisionChanges from './RevisionChanges';
import Disclosure from './Disclosure';

export default function InvestigationPanel({rec, onRevision}: {rec: RecommendationDetail; onRevision: (id: string) => void | Promise<void>}) {
  const [thread, setThread] = useState<Investigation[]>([]);
  const [question, setQuestion] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<'schedule' | 'staff' | null>(null);
  const [at, setAt] = useState('');
  const [employee, setEmployee] = useState('role');
  const candidates = useAsync(() => api.staffCandidates(rec.id), [rec.id]);
  const chips = useAsync(() => api.suggestions(rec.id), [rec.id, thread.length]);
  const health = useAsync(() => api.health(), []);
  const callback = useRef(onRevision);
  callback.current = onRevision;
  const seen = useRef(new Set<string>());
  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const rows = await api.investigations(rec.id);
        if (cancelled) return;
        setThread(Array.from(new Map(rows.map(r => [r.id, r])).values()));
        // The latest revision wins; never navigate back to an older thread entry.
        const revised = rows.filter(r => r.revised_recommendation_id).at(-1);
        if (revised) {
          const key = `${revised.revised_recommendation_id}:${revised.status}:${revised.result.new_evidence_ids?.join(',')}`;
          if (!seen.current.has(key)) {
            await callback.current(revised.revised_recommendation_id!);
            seen.current.add(key);
          }
        }
        if (!cancelled) setProgressError(null);
      } catch (e) { if (!cancelled) setProgressError(`Unable to load investigation progress: ${e instanceof Error ? e.message : String(e)}. Retrying…`); }
      finally {inFlight = false;}
    };
    load();
    const timer = setInterval(load, 1200);
    return () => {cancelled = true; clearInterval(timer);};
  }, [rec.id]);
  const budget = thread.at(-1)?.budget ?? chips.data?.budget;
  const running = thread.some(i => ['pending', 'running'].includes(i.status));
  const day = new Date(`${rec.operating_date}T12:00:00`).toLocaleDateString(undefined, {weekday: 'long'});
  async function submit(body: InvestigationRequest) {
    setBusy(true); setError(null);
    try {
      const accepted = await api.investigate(rec.id, body);
      // Show the accepted work immediately, before the first polling round trip.
      setThread(rows => [...rows.filter(r => r.id !== accepted.investigation_id), {
        id: accepted.investigation_id, recommendation_id: rec.id, question: body.question ?? body.action!.replace(/_/g, ' '),
        action: body.action ?? null, status: 'pending', created_at: new Date().toISOString(),
        revised_recommendation_id: null, budget: budget ?? {used: 0, limit: 12, label: '0 of 12 checks used'},
        result: {}, staff_questions: [],
      }]);
      setQuestion(''); setForm(null);
      chips.reload();
    } catch(e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  return <aside className="space-y-4">
    <Card title="Ask for more evidence">
      <p className="text-sm text-slate-600">All checks concern {day}, {rec.operating_date}, and its travel/work windows.</p>
      {(busy || running || rec.status === 'preliminary') && <div role="status" aria-live="polite"><Loading label={busy ? 'Submitting investigation…' : running ? 'Investigation in progress — live steps below' : 'Evidence check in progress — waiting for the final recommendation'} /></div>}
      {budget && <div className="my-3"><div className="text-sm font-semibold">{budget.label}</div><meter className="w-full" min={0} max={budget.limit} value={budget.used} /><p className="text-xs text-slate-500">Model calls share this budget across revisions. One-tap checks and record explanations stay available.</p></div>}
      <div className="my-3 flex flex-wrap gap-2">{chips.data?.suggestions.slice(0,4).map(c => <button key={c.action} className="rounded-full border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100 disabled:opacity-50" disabled={busy || running || rec.status === 'preliminary'} onClick={() => c.action === 'schedule_recheck' ? setForm('schedule') : submit({action: c.action})}>{c.label}</button>)}</div>
      <form onSubmit={e => {e.preventDefault(); submit({question});}} className="space-y-2">
        <textarea aria-label="Investigation question" className="w-full rounded border border-slate-300 p-2 text-sm" maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} placeholder={`Ask about ${day}'s conditions…`} rows={3} />
        <Button disabled={!question.trim() || busy || running || rec.status === 'preliminary'} type="submit">Ask</Button>
      </form>
      <details className="mt-3"><summary>More checks · staff questions and scheduling</summary><div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => setForm('schedule')}>Schedule recheck</Button>
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => setForm('staff')}>Ask staff on site</Button>
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => submit({action: 'check_river'})}>Check river</Button>
      {chips.data?.suggestions.slice(4).map(c => <Button key={c.action} variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => submit({action: c.action})}>{c.label}</Button>)}</div></details>
      {form === 'schedule' && <form className="mt-3 space-y-2" onSubmit={e => {e.preventDefault(); submit({action: 'schedule_recheck', at: new Date(at).toISOString()});}}>
        <label className="text-sm">Recheck time (your browser's local timezone)<input aria-label="Recheck time" type="datetime-local" required value={at} onChange={e => setAt(e.target.value)} className="block w-full rounded border p-2" /></label>
        <Button type="submit" disabled={busy}>Schedule for this target day</Button>
      </form>}
      {form === 'staff' && <form className="mt-3 space-y-2" onSubmit={e => {e.preventDefault(); submit({action: 'ask_staff', question: question || `Are site conditions unsafe during ${rec.operating_date}'s commute window?`, ...(employee === 'role' ? {role: 'facility_ops'} : {employee_id: employee}), options: ['Unsafe', 'Safe', 'Unsure']});}}>
        <label className="text-sm">Recipient<select aria-label="Staff recipient" className="block w-full rounded border p-2" value={employee} onChange={e => setEmployee(e.target.value)}><option value="role">All assigned facility operations staff</option>{candidates.data?.map(p => <option key={p.id} value={p.id}>{p.name} ({humanText(p.role)})</option>)}</select></label>
        <p className="text-xs text-slate-600">Uses the question above, or asks whether the site is unsafe during this day's commute. Replies: Unsafe / Safe / Unsure.</p>
        <Button type="submit" disabled={busy || !candidates.data?.length}>Send evidence request</Button>
      </form>}
      {error && <ErrorBox error={error} />}
      {progressError && <ErrorBox error={progressError} />}
      {chips.error && <ErrorBox error={`Suggestions unavailable: ${chips.error}`} onRetry={chips.reload} />}
    </Card>
    <RevisionChanges rec={rec} />
    <div className="space-y-3" aria-label="Investigation thread">{[false, true].map(previous => {
      const rows = thread.filter((i, index) => previous === (!['pending', 'running', 'waiting_staff'].includes(i.status) && index !== thread.length - 1));
      const cards = rows.map(i => <Card key={i.id} title={humanText(i.question.replaceAll('_', ' '))}>
      <p className="text-xs text-slate-500">{i.status.replace(/_/g, ' ')}</p>
      {['pending','running'].includes(i.status) && <div role="status"><Loading label={i.status === 'pending' ? 'Queued — waiting to start' : 'Checking evidence…'} /></div>}
      {['pending','running'].includes(i.status) && <AgentActivity evaluationId={i.id} live />}
      {i.result.source_checks && <><h3 className="mt-2 font-semibold">Sources checked</h3><ul className="text-sm">{i.result.source_checks.map((s,j) => <li key={j}>{s.status === 'failed' ? '⚠' : '✓'} {s.source} — {s.status}</li>)}</ul><p className="mt-2 text-sm"><strong>What’s new:</strong> {i.result.source_checks.filter(s => ['new','changed'].includes(s.status)).map(s => s.source).join(', ') || 'No changed local sources.'}</p></>}
      {i.result.forecaster_summary && <div className="mt-2 text-sm"><strong>AI forecaster summary · quote verified</strong><p>Target day mentioned: {i.result.forecaster_summary.mentions_target_day ? 'yes' : 'no'} · Forecaster confidence: {i.result.forecaster_summary.forecaster_confidence}</p><p>Hazards mentioned: {i.result.forecaster_summary.hazards_mentioned.join(', ') || 'None stated in this quote'}</p><blockquote>{i.result.forecaster_summary.supporting_quote}</blockquote><a className="underline" href={`/api/snapshots/${i.result.forecaster_summary.saved_copy_hash}`}>Saved forecaster product</a></div>}
      {!!i.result.tools_used?.length && <details className="my-2 text-sm"><summary>Checks performed</summary>{i.result.tools_used.map((tool, j) => <p key={j}>{toolSentence(tool)}</p>)}</details>}
      {i.result.answer && <><p className="mt-2 text-sm leading-relaxed"><CitedAnswer text={i.result.answer} /></p><p className="mt-2 text-xs font-semibold text-slate-600">{i.result.source_label}</p></>}
      <details className="mt-2 text-sm"><summary>All evidence and policy citations</summary><div className="flex flex-wrap gap-2">{Array.from(new Set([...(i.result.evidence_ids ?? []), ...(i.result.new_evidence_ids ?? [])])).map((id, j) => <a className="underline" key={id} href={`/saved-evidence/${id}`} target="_blank" rel="noreferrer">Saved evidence {j + 1}</a>)}{i.result.policy_ids?.map(id => <Link className="underline" key={id} to={`/thresholds#${id}`}>{ruleLabel(id)}</Link>)}</div></details>
      {i.result.limitations?.map((l,j) => <p key={j} className="mt-1 text-xs text-amber-800">{humanText(l)}</p>)}
      {i.result.could_not_answer_because && <p className="mt-1 text-xs text-amber-800">Could not answer: {humanText(i.result.could_not_answer_because)}</p>}
      {i.staff_questions.map(s => <div key={s.id} className="mt-3 rounded border border-slate-200 bg-slate-50 p-2 text-xs"><strong>{candidates.data?.find(p => p.id === s.employee_id)?.name ?? s.employee_id}: {s.status}</strong><p>{s.question}</p><p>{s.response ?? `Expires ${new Date(s.expires_at).toLocaleString()}`}</p>{!s.delivery.ok && <p className="text-amber-800">Push not delivered: {s.delivery.error}</p>}
        {health.data?.demo_mode && s.status === 'pending' && <div className="mt-2"><p>SIMULATED for the demo: answer on this persona's behalf</p>{s.options.map(o => <button key={o} className="mr-2 rounded border bg-yellow-100 px-2 py-1" onClick={async () => {try {const r = await api.demoStaffResponse(s.id,o); onRevision(r.recommendation_id);} catch(e) {setError(String(e));}}}>{o}</button>)}</div>}
      </div>)}
    </Card>);
      return previous ? rows.length > 0 && <Disclosure key="previous" title={`Previous questions (${rows.length})`} summary="Saved answers and evidence links">{cards}</Disclosure> : <div key="current">{cards}</div>;
    })}</div>
  </aside>;
}

export function CitedAnswer({text}: {text: string}) {
  const parts = text.split(/(\[(?:evidence:)?[a-f0-9]{32}\]|\[policy:[\w-]+\])/g);
  let evidenceIndex = 0;
  return <>{parts.map((part, j) => {
    const evidence = part.match(/^\[(?:evidence:)?([a-f0-9]{32})\]$/);
    if (evidence) return <a key={j} className="underline" href={`/saved-evidence/${evidence[1]}`} title="Saved evidence">[{++evidenceIndex}]</a>;
    const policy = part.match(/^\[policy:([\w-]+)\]$/);
    if (policy) return <Link key={j} className="underline" title={ruleLabel(policy[1])} to={`/thresholds#${policy[1]}`}>[{++evidenceIndex}]</Link>;
    return <span key={j}>{humanText(part)}</span>;
  })}</>;
}
