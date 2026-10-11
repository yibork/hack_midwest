import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Investigation, type InvestigationRequest, type RecommendationDetail } from '../api';
import AgentActivity from './AgentActivity';
import { Button, Card, ErrorBox, Loading, useAsync } from './ui';
import RevisionChanges from './RevisionChanges';

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
        setThread(rows);
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
      <div className="my-3 flex flex-wrap gap-2">{chips.data?.suggestions.map(c => <button key={c.action} className="rounded-full border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100 disabled:opacity-50" disabled={busy || running || rec.status === 'preliminary'} onClick={() => c.action === 'schedule_recheck' ? setForm('schedule') : submit({action: c.action})}>{c.label}</button>)}</div>
      <form onSubmit={e => {e.preventDefault(); submit({question});}} className="space-y-2">
        <textarea aria-label="Investigation question" className="w-full rounded border border-slate-300 p-2 text-sm" maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} placeholder={`Ask about ${day}'s conditions…`} rows={3} />
        <Button disabled={!question.trim() || busy || running || rec.status === 'preliminary'} type="submit">Ask</Button>
      </form>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => setForm('schedule')}>Schedule recheck</Button>
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => setForm('staff')}>Ask staff on site</Button>
        <Button variant="secondary" disabled={busy || running || rec.status === 'preliminary'} onClick={() => submit({action: 'check_river'})}>Check river</Button>
      </div>
      {form === 'schedule' && <form className="mt-3 space-y-2" onSubmit={e => {e.preventDefault(); submit({action: 'schedule_recheck', at: new Date(at).toISOString()});}}>
        <label className="text-sm">Recheck time (your browser's local timezone)<input aria-label="Recheck time" type="datetime-local" required value={at} onChange={e => setAt(e.target.value)} className="block w-full rounded border p-2" /></label>
        <Button type="submit" disabled={busy}>Schedule for this target day</Button>
      </form>}
      {form === 'staff' && <form className="mt-3 space-y-2" onSubmit={e => {e.preventDefault(); submit({action: 'ask_staff', question: question || `Are site conditions unsafe during ${rec.operating_date}'s commute window?`, ...(employee === 'role' ? {role: 'facility_ops'} : {employee_id: employee}), options: ['Unsafe', 'Safe', 'Unsure']});}}>
        <label className="text-sm">Recipient<select aria-label="Staff recipient" className="block w-full rounded border p-2" value={employee} onChange={e => setEmployee(e.target.value)}><option value="role">All assigned facility operations staff</option>{candidates.data?.map(p => <option key={p.id} value={p.id}>{p.name} ({p.role})</option>)}</select></label>
        <p className="text-xs text-slate-600">Uses the question above, or asks whether the site is unsafe during this day's commute. Replies: Unsafe / Safe / Unsure.</p>
        <Button type="submit" disabled={busy || !candidates.data?.length}>Send evidence request</Button>
      </form>}
      {error && <ErrorBox error={error} />}
      {progressError && <ErrorBox error={progressError} />}
      {chips.error && <ErrorBox error={`Suggestions unavailable: ${chips.error}`} onRetry={chips.reload} />}
    </Card>
    <RevisionChanges rec={rec} />
    <div className="space-y-3" aria-label="Investigation thread">{thread.map(i => <Card key={i.id} title={i.question}>
      <p className="text-xs text-slate-500">{i.status.replace(/_/g, ' ')}</p>
      {['pending','running'].includes(i.status) && <div role="status"><Loading label={i.status === 'pending' ? 'Queued — waiting to start' : 'Checking evidence…'} /></div>}
      <AgentActivity evaluationId={i.id} live={['pending','running'].includes(i.status)} />
      {!!i.result.tools_used?.length && <p className="my-1 text-xs text-slate-600">Tools: {i.result.tools_used.join(', ')}</p>}
      {i.result.answer && <><p className="mt-2 text-sm leading-relaxed">{i.result.answer}</p><p className="mt-2 text-xs font-semibold text-indigo-800">{i.result.source_label}</p></>}
      <div className="mt-2 flex flex-wrap gap-2 text-xs">{Array.from(new Set([...(i.result.evidence_ids ?? []), ...(i.result.new_evidence_ids ?? [])])).map(id => <a className="underline" key={id} href={`/api/evidence/${id}`} target="_blank" rel="noreferrer">Evidence {id.slice(0,8)}</a>)}{i.result.policy_ids?.map(id => <Link className="underline" key={id} to={`/thresholds#${id}`}>{id}</Link>)}</div>
      {i.result.limitations?.map((l,j) => <p key={j} className="mt-1 text-xs text-amber-800">{l}</p>)}
      {i.result.could_not_answer_because && <p className="mt-1 text-xs text-amber-800">Could not answer: {i.result.could_not_answer_because}</p>}
      {i.staff_questions.map(s => <div key={s.id} className="mt-3 rounded border border-slate-200 bg-slate-50 p-2 text-xs"><strong>{candidates.data?.find(p => p.id === s.employee_id)?.name ?? s.employee_id}: {s.status}</strong><p>{s.question}</p><p>{s.response ?? `Expires ${new Date(s.expires_at).toLocaleString()}`}</p>{!s.delivery.ok && <p className="text-amber-800">Push not delivered: {s.delivery.error}</p>}
        {health.data?.demo_mode && s.status === 'pending' && <div className="mt-2"><p>SIMULATED for the demo: answer on this persona's behalf</p>{s.options.map(o => <button key={o} className="mr-2 rounded border bg-yellow-100 px-2 py-1" onClick={async () => {try {const r = await api.demoStaffResponse(s.id,o); onRevision(r.recommendation_id);} catch(e) {setError(String(e));}}}>{o}</button>)}</div>}
      </div>)}
    </Card>)}</div>
  </aside>;
}
