import {act, cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {api, type Decision, type NoticeDetail} from '../api';
import NoticePanel from './NoticePanel';

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.useRealTimers();});
it('confirmation polls live counts every three seconds and links to the memo', async () => {
  vi.useFakeTimers();
  const detail = {notice: {id: 'notice', sent_at: '2026-10-13T10:00:00Z', params: {}, version: 1},
    previous: null, coverage: [], notifications: [], names: {}, clock: {at: null, label: 'No action needed'}} as unknown as NoticeDetail;
  vi.spyOn(api, 'decisionNotice').mockResolvedValue(detail);
  render(<NoticePanel decision={{id: 'decision', final_posture: 'closure'} as Decision} tz="UTC" onSending={() => {}} />);
  await act(async () => {});
  expect(screen.getByText('Confirmed · Live delivery and acknowledgments')).toBeTruthy();
  expect(screen.getByText('View decision memo').getAttribute('href')).toBe('/decisions/decision/memo');
  await act(async () => {vi.advanceTimersByTime(3000);});
  expect(api.decisionNotice).toHaveBeenCalledTimes(2);
});

it('counts real channels separately from simulated staff and provider failures', async () => {
  const result = (status: string, ok = false) => ({status, ok, error: ok ? null : 'provider message', sent_at: ok ? 'now' : null});
  const detail = {notice: {id: 'notice', sent_at: '2026-10-13T10:00:00Z', params: {}, version: 1},
    previous: null, coverage: [], names: {}, clock: {at: null, label: 'No action needed'}, notifications: [
      {included: true, channel_results: {push: result('delivered', true), email: result('delivered', true)}},
      {included: true, channel_results: {push: result('simulated'), email: result('simulated')}},
      {included: true, channel_results: {email: result('failed')}},
    ]} as unknown as NoticeDetail;
  vi.spyOn(api, 'decisionNotice').mockResolvedValue(detail);
  render(<NoticePanel decision={{id: 'decision', final_posture: 'closure'} as Decision} tz="UTC" onSending={() => {}} />);
  expect(await screen.findByText('Delivered: 2 real (1 push, 1 email) · 2 simulated (synthetic staff) · 1 failed')).toBeTruthy();
});
