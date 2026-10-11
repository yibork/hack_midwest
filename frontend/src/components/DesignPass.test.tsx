import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import type { RecommendationDetail } from '../api';
import { ConfidenceBadge, PolicyDetails, humanText, postureLabel } from './ui';
import RecommendationHero from './RecommendationHero';
import { NearestThresholds } from './RecommendationView';
import { CitedAnswer } from './InvestigationPanel';
import { toolSentence } from './AgentActivity';
import { CoverageStrip } from './People';

afterEach(cleanup);
const rec = {id: 'r', facility: {timezone: 'UTC'}, posture: 'normal', confidence: 'low', operating_date: '2026-10-13',
  confidence_reasons: ['Forecast is 56 h out'], triggers: [], lead_time_hours: 56, policy_version: '1.0.0+1234abcd',
  what_would_change: [], decide_by: null} as unknown as RecommendationDetail;

it('uses human posture labels and plain tool sentences', () => {
  expect(postureLabel('normal')).toBe('Normal operations');
  expect(postureLabel('delayed_arrival')).toBe('Delayed arrival');
  expect(toolSentence('check_access_routes')).toBe('Checked nearby facility access routes');
  expect(humanText("Rule 'high_wind' fired; delayed_arrival under 1.0.0+1234abcd")).not.toMatch(/high_wind|delayed_arrival|1234abcd/);
});

it('low confidence is neutral, not an alarm colour', () => {
  const {container} = render(<ConfidenceBadge confidence="low" />);
  expect(container.querySelector('.confidence-low')).toBeTruthy();
  expect(container.innerHTML).not.toContain('red');
});

it('keeps the exact policy hash in a closed disclosure', () => {
  render(<PolicyDetails version={rec.policy_version} />);
  expect(screen.getByText('Policy 1.0')).toBeTruthy();
  expect(screen.getByText(rec.policy_version).closest('details')?.open).toBe(false);
});

it('shows a single outcome, inline confidence reason and no-action deadline', () => {
  render(<RecommendationHero rec={rec} />);
  expect(screen.getByText('Normal operations')).toBeTruthy();
  expect(screen.getByText('No action needed')).toBeTruthy();
  expect(screen.getByText('Forecast is 56 h out')).toBeTruthy();
  expect(screen.getByText('56 h')).toBeTruthy();
});

it('shows the nearest two thresholds with observed progress, then expands', () => {
  const lines = ['Gusts reaching 58 mph would move this to closure; forecast peak is 11.5 mph.',
    'Gusts reaching 46 mph would move this to delayed_arrival; forecast peak is 11.5 mph.', 'Flood Warning would change the posture.'];
  render(<MemoryRouter><NearestThresholds rec={{...rec, what_would_change: lines, change_rule_ids: ['high_wind', 'wind_advisory', 'flood']}} /></MemoryRouter>);
  expect(screen.getAllByRole('progressbar')).toHaveLength(2);
  expect(screen.getAllByRole('progressbar')[0].getAttribute('aria-valuenow')).toBe('11.5');
  expect(screen.getAllByRole('link')[0].getAttribute('href')).toBe('/thresholds#wind_advisory');
  expect(screen.queryByText('Flood Warning would change the posture.')).toBeNull();
  fireEvent.click(screen.getByText('Show all'));
  expect(screen.getByText('Flood Warning would change the posture.')).toBeTruthy();
});

it('turns inline evidence IDs and policy codes into readable linked citations', () => {
  const id = 'e'.repeat(32);
  render(<MemoryRouter><CitedAnswer text={`Current normal. [evidence:${id}] [policy:lead_time_confidence]`} /></MemoryRouter>);
  expect(screen.getByText('[1]').getAttribute('href')).toBe(`/saved-evidence/${id}`);
  expect(screen.getByText('[2]').getAttribute('href')).toBe('/thresholds#lead_time_confidence');
  expect(document.body.textContent).not.toContain(id);
});

it('all-covered staffing is quiet and expandable', () => {
  render(<CoverageStrip coverage={[{code: 'animal_care', label: 'Animal care 2/2 - covered', status: 'covered', available: ['Dana'], exposed: []}] as unknown as Parameters<typeof CoverageStrip>[0]['coverage']} />);
  expect(screen.getByText('✓ All 1 critical functions covered')).toBeTruthy();
  expect(screen.getByText('Staffing details').closest('details')?.open).toBe(false);
});
