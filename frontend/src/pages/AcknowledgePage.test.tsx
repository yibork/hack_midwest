import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type Notification } from '../api';
import AcknowledgePage from './AcknowledgePage';

afterEach(() => {cleanup(); vi.restoreAllMocks();});
it('records only when the one large acknowledgment button is pressed', async () => {
  vi.spyOn(api, 'ack').mockResolvedValue({employee_id: 'emp-005', duty_instruction: 'Report safely at 05:00', acknowledged_at: '2026-10-10T09:00:00Z'} as Notification);
  render(<MemoryRouter initialEntries={['/acknowledge/notice']}><Routes><Route path="/acknowledge/:id" element={<AcknowledgePage />} /></Routes></MemoryRouter>);
  expect(api.ack).not.toHaveBeenCalled();
  expect(screen.getAllByRole('button')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByText('Report safely at 05:00')).toBeTruthy();
  expect(screen.getByText('View my status').getAttribute('href')).toBe('/me?as=emp-005');
});

it('retains the SIMULATED label when it is in the structured message footer', async () => {
  vi.spyOn(api, 'ack').mockResolvedValue({employee_id: 'emp-005', duty_instruction: 'Report at 05:00',
    acknowledged_at: '2026-10-10T09:00:00Z', message: 'USDA demo office: closure.\nDemo: synthetic office, SIMULATED alert'} as Notification);
  render(<MemoryRouter initialEntries={['/acknowledge/notice']}><Routes><Route path="/acknowledge/:id" element={<AcknowledgePage />} /></Routes></MemoryRouter>);
  fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByText('SIMULATED · Not a live operating instruction')).toBeTruthy();
});
