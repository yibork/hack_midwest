import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import RecommendationView from './RecommendationView';
import type { RecommendationDetail } from '../api';
import {PeopleStrip} from './People';
import type {PeopleSummary} from '../api';
vi.mock('./MapView', () => ({ default: () => null }));
afterEach(cleanup);
it('hides staffing and reports essential staff when the feature is off', () => {
  const people = {staffing_feature: false, emergency_staff_reporting: 2, coverage: [{status: 'shortfall', label: 'Animal care 1/2 - shortfall'}],
    action_required: 4, fyi: 2, params: {}, exposed_count: 1, minor_commute_count: 0} as unknown as PeopleSummary;
  render(<PeopleStrip people={people} />);
  expect(screen.queryByText(/shortfall/)).toBeNull();
  expect(screen.queryByLabelText('Critical function staffing')).toBeNull();
  expect(screen.getByText(/emergency staff reporting/).textContent).toBe('2 emergency staff reporting');
  expect(screen.getByText(/must act/).textContent).toBe('4 must act');
});
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

it('shows unavailable relocated exposure instead of zero exposed commutes', () => {
  const people = {staffing_feature: true, coverage: [], action_required: 4, fyi: 2, params: {}, exposed_count: 0, minor_commute_count: 0,
    exposure_status: 'unknown', exposure_note: 'Home-zone exposure: not available for this location'} as unknown as PeopleSummary;
  render(<PeopleStrip people={people} />);
  expect(screen.getByText('Home-zone exposure: not available for this location')).toBeTruthy();
  expect(screen.queryByText(/with an exposed commute/)).toBeNull();
});
