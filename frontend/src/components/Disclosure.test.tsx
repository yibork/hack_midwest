import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import Disclosure from './Disclosure';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it('shows collapsed summaries, remembers expansion, and survives unavailable storage', async () => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value)});
  const view = render(<Disclosure title="Evidence" summary="15 sources, all current"><p>Source details</p></Disclosure>);
  expect(screen.getByText('15 sources, all current')).toBeTruthy();
  expect(screen.queryByText('Source details')).toBeNull();
  fireEvent.click(screen.getByText('Evidence'));
  await screen.findByText('Source details');
  view.unmount();
  render(<Disclosure title="Evidence" summary="15 sources, all current"><p>Source details</p></Disclosure>);
  expect(screen.getByText('Source details')).toBeTruthy();
  cleanup();
  vi.stubGlobal('localStorage', {getItem: () => {throw new Error('unavailable');}, setItem: () => {throw new Error('unavailable');}});
  render(<Disclosure title="Evidence" summary="15 sources, all current"><p>Source details</p></Disclosure>);
  fireEvent.click(screen.getByText('Evidence'));
  await screen.findByText('Source details');
});
