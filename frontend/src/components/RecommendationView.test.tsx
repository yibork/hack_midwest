import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import RecommendationView from './RecommendationView';
import type { RecommendationDetail } from '../api';
vi.mock('./MapView', () => ({ default: () => null }));
afterEach(cleanup);
it('links each trigger and change line to the rule catalog', async () => {
  const rec = { id: 'r', facility: { timezone: 'UTC', temporary: true }, operating_date: '2026-10-13',
    posture: 'closure', confidence: 'low', confidence_reasons: [], what_would_change: ['Gusts reaching 58 mph would move this to closure.'],
    change_rule_ids: ['high_wind'], evidence: [], triggers: [{ rule_id: 'high_wind', description: 'Wind', posture: 'closure', citation: { title: 'EAX' } }],
    policy_version: '1.0.0', evaluated_at: '2026-10-11T00:00:00Z', rationale: 'Summary' } as unknown as RecommendationDetail;
  render(<MemoryRouter><RecommendationView rec={rec} /></MemoryRouter>);
  expect(screen.getByText('Triggers').closest('details')?.open).toBe(false);
  expect(screen.getByText('1 policy thresholds met')).toBeTruthy();
  fireEvent.click(screen.getByText('Triggers'));
  await screen.findByText(/View threshold and verification/);
  expect(screen.getByText(/View threshold and verification/).closest('a')?.getAttribute('href')).toBe('/thresholds#high_wind');
  expect(screen.getByRole('link', {name: 'Threshold'}).getAttribute('href')).toBe('/thresholds#high_wind');
});
