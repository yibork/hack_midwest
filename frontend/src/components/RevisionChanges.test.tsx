import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { type RecommendationDetail, type RevisionDiff } from '../api';
import RevisionChanges from './RevisionChanges';

afterEach(cleanup);
const rec = {id: 'new', posture: 'normal', confidence: 'low', evaluated_at: '2026-10-11T02:00:00Z',
  facility: {timezone: 'America/Chicago'}, revision_context: {kind: 'investigation', label: 'Investigation question',
  question: 'Are access routes open?', at: '2026-10-11T02:00:00Z'}} as RecommendationDetail;
const diff = (changes: RevisionDiff['changes']): RevisionDiff => ({changed: true, changes,
  recipient_counts: {before: {action_required: 0, fyi: 0}, after: {action_required: 0, fyi: 0}}});

it('renders unchanged confidence with added reason, plain recipient counts, cause and time', () => {
  render(<RevisionChanges rec={{...rec, revision_diff: diff({confidence_reasons: {before: ['Long lead time'],
    after: ['Long lead time', 'River forecast unavailable']}})}} />);
  expect(screen.getByText('Changed since last look')).toBeTruthy();
  expect(screen.getByText('Confidence: low (unchanged) — reason added: River forecast unavailable')).toBeTruthy();
  expect(screen.getByText('Recipients: 0 → 0 (no change)')).toBeTruthy();
  expect(screen.getByText(/Investigation question: “Are access routes open\?”/)).toBeTruthy();
  expect(document.body.textContent).not.toContain('{');
});

it('hides unchanged checks and trigger-ID-only bookkeeping', () => {
  const t = {rule_id: 'wind_advisory', description: 'Wind advisory', observed: 47, threshold: 46};
  render(<RevisionChanges rec={{...rec, revision_diff: diff({triggers: {
    before: [{...t, evidence_id: 'old'}], after: [{...t, evidence_id: 'new'}]}})}} />);
  expect(screen.queryByText('Changed since last look')).toBeNull();
});

it('describes trigger, recipient and posture changes without JSON', () => {
  const d = diff({posture: {before: 'normal', after: 'delayed_arrival'},
    triggers: {before: [], after: [{rule_id: 'wind_advisory', description: 'Commute gusts reach advisory criteria', observed: 47, units: 'mph'}]}});
  d.recipient_counts = {before: {action_required: 0, fyi: 0}, after: {action_required: 3, fyi: 2}};
  render(<RevisionChanges rec={{...rec, posture: 'delayed_arrival', revision_diff: d}} />);
  expect(screen.getByText(/Recipients: 0 → 5/)).toBeTruthy();
  expect(screen.getByText(/Trigger added: Commute gusts reach advisory criteria — observed 47 mph/)).toBeTruthy();
  expect(document.body.textContent).not.toContain('"rule_id"');
});

it('identifies a scheduled job separately from the target-day explanation', () => {
  render(<RevisionChanges rec={{...rec, revision_context: {kind: 'scheduled_job', label: 'Scheduled job',
    question: 'Morning recheck', at: '2026-10-11T02:00:00Z'}, revision_diff: diff({confidence: {before: 'low', after: 'medium'}})}} />);
  expect(screen.getByText(/Scheduled job: “Morning recheck”/)).toBeTruthy();
});
