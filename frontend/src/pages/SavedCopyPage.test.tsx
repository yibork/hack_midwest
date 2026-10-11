import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type SnapshotVerification, type Evidence } from '../api';
import SavedCopyPage from './SavedCopyPage';
import SavedCopyLinks from '../components/SavedCopyLinks';
import RecordVerification from '../components/RecordVerification';

afterEach(() => {cleanup(); vi.restoreAllMocks();});
const digest = 'a'.repeat(64);
const info: SnapshotVerification = {sha256: digest, actual_sha256: digest, match: true, byte_count: 10,
  copies: [{source_url: 'https://api.weather.gov/test', retrieved_at: '2026-10-10T10:00:00Z', source_time: null,
    source: 'demo', kind: 'wind_gust', copy_kind: 'normalized', scenario_kind: 'simulated'}]};

it('shows provenance and SIMULATED labels and renders hostile source as inert text', async () => {
  vi.spyOn(api, 'snapshotText').mockResolvedValue('{"source":"<script>alert(1)</script>"}');
  vi.spyOn(api, 'snapshotVerify').mockResolvedValue(info);
  const {container} = render(<MemoryRouter initialEntries={[`/saved-copy/${digest}`]}><Routes><Route path="/saved-copy/:digest" element={<SavedCopyPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText('✓ Hash matches — saved bytes unchanged')).toBeTruthy();
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('pre')?.textContent).toContain('<script>alert(1)</script>');
  expect(screen.getByText(/SIMULATED/)).toBeTruthy();
  expect(screen.getByText(/original raw response was not stored/)).toBeTruthy();
});

it('recomputes on request and clearly shows mismatch', async () => {
  vi.spyOn(api, 'snapshotText').mockResolvedValue('stored bytes');
  const verify = vi.spyOn(api, 'snapshotVerify').mockResolvedValue(info);
  render(<MemoryRouter initialEntries={[`/saved-copy/${digest}`]}><Routes><Route path="/saved-copy/:digest" element={<SavedCopyPage />} /></Routes></MemoryRouter>);
  await screen.findByText('✓ Hash matches — saved bytes unchanged');
  verify.mockResolvedValue({...info, match: false, actual_sha256: 'b'.repeat(64)});
  fireEvent.click(screen.getByRole('button', {name: 'Verify saved bytes'}));
  expect(await screen.findByText('Hash mismatch — saved bytes changed')).toBeTruthy();
  expect(verify.mock.calls.length).toBeGreaterThan(1);
});

it('offers both source and saved-copy links with normalized-copy caveat', () => {
  render(<SavedCopyLinks evidence={{url: 'https://api.weather.gov/test', snapshot_hash: null,
    saved_copy_hash: digest, saved_copy_kind: 'normalized'} as Evidence} />);
  expect(screen.getByText('Saved copy').getAttribute('href')).toBe(`/api/snapshots/${digest}`);
  expect(screen.getByText('Source (live — may have changed)').getAttribute('href')).toBe('https://api.weather.gov/test');
  expect(screen.getByText(/no original raw response saved/)).toBeTruthy();
});

it('shows the first broken record event', async () => {
  vi.spyOn(api, 'recordVerify').mockResolvedValue({ok: false, event_count: 3, first_broken_event_id: 7,
    reason: 'Event content hash does not match', head_event_id: 6, head_hash: digest});
  render(<RecordVerification facilityId="facility" />);
  expect(await screen.findByText('Record broken at event 7: Event content hash does not match')).toBeTruthy();
});
