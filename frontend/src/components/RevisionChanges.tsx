import { type RecommendationDetail, type Trigger } from '../api';
import { Card, fmtDateTime, postureLabel } from './ui';

const strings = (v: unknown): string[] => Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : [];
const triggers = (v: unknown): Trigger[] => Array.isArray(v) ? v : [];
const triggerFacts = (v: unknown) => triggers(v).map(t => [t.rule_id, t.posture, t.description, t.threshold, t.observed, t.margin, t.units]).sort();
const counts = (v: unknown) => {
  const c = (v ?? {}) as {action_required?: number; fyi?: number};
  return {action: c.action_required ?? 0, fyi: c.fyi ?? 0};
};
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export default function RevisionChanges({rec}: {rec: RecommendationDetail}) {
  const diff = rec.revision_diff;
  if (!diff) return null;
  const meaningful = Object.entries(diff.changes).filter(([key, c]) =>
    key === 'triggers' ? !same(triggerFacts(c.before), triggerFacts(c.after)) :
    key === 'recipient_counts' ? !same(counts(c.before), counts(c.after)) : !same(c.before, c.after));
  if (!meaningful.length) return null;
  const oldRecipients = counts(diff.recipient_counts.before), newRecipients = counts(diff.recipient_counts.after);
  const oldTotal = oldRecipients.action + oldRecipients.fyi, newTotal = newRecipients.action + newRecipients.fyi;
  const context = rec.revision_context;
  const reasons = diff.changes.confidence_reasons;
  const added = reasons ? strings(reasons.after).filter(s => !strings(reasons.before).includes(s)) : [];
  const removed = reasons ? strings(reasons.before).filter(s => !strings(reasons.after).includes(s)) : [];
  const confidence = diff.changes.confidence;
  return <Card title="Changed since last look">
    <p className="text-sm">{context?.label ?? 'Evidence check'}{context?.question ? `: “${context.question}”` : ''}
      {(context?.at ?? rec.evaluated_at) && <> · {fmtDateTime(context?.at ?? rec.evaluated_at, rec.facility?.timezone ?? 'UTC')}</>}</p>
    <p className="mt-2 text-sm">{diff.changes.posture
      ? `Posture: ${postureLabel(diff.changes.posture.before as RecommendationDetail['posture'])} → ${postureLabel(rec.posture)}`
      : `Posture: ${postureLabel(rec.posture)} (unchanged)`}</p>
    <p className="mt-1 text-sm">Confidence: {confidence ? `${confidence.before} → ${confidence.after}` : `${rec.confidence} (unchanged)`}
      {added.length > 0 && ` — reason added: ${added.join('; ')}`}{removed.length > 0 && ` — reason removed: ${removed.join('; ')}`}</p>
    <p className="mt-1 text-sm">Recipients: {oldTotal} → {newTotal}{same(oldRecipients, newRecipients) ? ' (no change)' : ` (action required: ${oldRecipients.action} → ${newRecipients.action}; FYI: ${oldRecipients.fyi} → ${newRecipients.fyi})`}</p>
    {meaningful.filter(([field]) => field === 'triggers').flatMap(([, c]) => {
      const old = triggers(c.before), next = triggers(c.after);
      return [
        ...next.filter(t => !old.some(o => same(triggerFacts([o]), triggerFacts([t])))).map(t =>
          <p className="mt-2 text-sm" key={`trigger-${t.rule_id}`}>Trigger {old.some(o => o.rule_id === t.rule_id) ? 'updated' : 'added'}: {t.description}{t.observed != null ? ` — observed ${t.observed} ${t.units ?? ''}` : ''}</p>),
        ...old.filter(t => !next.some(n => n.rule_id === t.rule_id)).map(t => <p className="mt-2 text-sm" key={`removed-${t.rule_id}`}>Trigger removed: {t.description}</p>),
      ];
    })}
    {diff.changes.what_would_change && <div className="mt-2 text-sm">What would change:
      {strings(diff.changes.what_would_change.after).filter(s => !strings(diff.changes.what_would_change.before).includes(s)).map(s => <p key={s}>Added: {s}</p>)}
      {strings(diff.changes.what_would_change.before).filter(s => !strings(diff.changes.what_would_change.after).includes(s)).map(s => <p key={s}>Removed: {s}</p>)}
    </div>}
  </Card>;
}
