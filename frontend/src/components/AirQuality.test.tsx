import {render, screen, cleanup} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import AirQuality from './AirQuality';
import type {Evidence} from '../api';
afterEach(cleanup);

it('missing data is unknown and never reported as good', () => {
  render(<AirQuality evidence={[]} targetDay="2026-10-12" tz="UTC" />);
  expect(screen.getByText(/Unknown — no AirNow evidence/)).toBeTruthy();
});

it('shows pollutants, category, reporting area and saved copy without credentials', () => {
  const ev = {id: 'aq', source: 'airnow', kind: 'air_quality_current', status: 'ok', saved_copy_hash: 'abc',
    url: 'https://www.airnowapi.org/aq/observation/current/ziplatlong/?distance=25',
    value: {pollutants: [{pollutant: 'PM2.5', aqi: 64, category: 'Moderate', reporting_area: 'KC',
      observation_time: '2026-10-11T04:00:00Z', status: 'stale'}]}} as unknown as Evidence;
  render(<AirQuality evidence={[ev]} targetDay="2026-10-12" tz="UTC" />);
  expect(screen.getByText(/PM2.5 AQI 64 — Moderate, KC/)).toBeTruthy();
  expect(screen.getByText(/stale \/ unknown; not used as current/)).toBeTruthy();
  expect(screen.getByRole('link', {name: 'Saved copy'}).getAttribute('href')).toBe('/api/snapshots/abc');
  expect(screen.getByRole('link', {name: /Source/}).getAttribute('href')).not.toContain('API_KEY');
});
