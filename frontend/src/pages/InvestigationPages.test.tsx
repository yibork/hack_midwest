import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type Facility, type RecommendationDetail } from '../api';
import FacilityPage from './FacilityPage';
import DecidePage from './DecidePage';

vi.mock('../components/InvestigationPanel', () => ({default: () => <div>Shared investigation panel</div>}));
vi.mock('../components/AgentActivity', () => ({default: () => null}));
vi.mock('../components/RecommendationView', () => ({default: () => <div>Recommendation details</div>}));
const facility = {id: 'facility', name: 'Test office', timezone: 'America/Chicago',
  access_routes: [], open_time: '07:30', close_time: '16:00', temporary: false} as unknown as Facility;
const rec = {id: 'rec', facility, facility_id: facility.id, operating_date: '2026-10-13', status: 'final',
  posture: 'normal', confidence: 'low', triggers: [], decision: null, rationale: 'Calendar explanation',
  evaluated_at: '2026-10-11T00:00:00Z'} as unknown as RecommendationDetail;
beforeEach(() => {
  vi.spyOn(api, 'facilities').mockResolvedValue([facility]);
  vi.spyOn(api, 'latestRecommendation').mockResolvedValue(rec);
  vi.spyOn(api, 'recommendation').mockResolvedValue(rec);
  vi.spyOn(api, 'health').mockResolvedValue({status: 'ok', demo_mode: false});
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('mounts the shared investigation panel on Facility', async () => {
  render(<MemoryRouter><FacilityPage /></MemoryRouter>);
  expect(await screen.findByText('Shared investigation panel')).toBeTruthy();
});

it('mounts the same investigation panel on Decide', async () => {
  render(<MemoryRouter initialEntries={['/decide?recommendation=rec']}><DecidePage /></MemoryRouter>);
  expect(await screen.findByText('Shared investigation panel')).toBeTruthy();
});
