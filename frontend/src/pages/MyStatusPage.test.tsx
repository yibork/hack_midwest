import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type MyStatus, type Notification } from '../api';
import MyStatusPage from './MyStatusPage';

afterEach(() => {cleanup(); vi.restoreAllMocks();});

const status = {
  employee: {name: 'Dana R.', language: 'en'}, facility_timezone: 'America/New_York',
  notification: {id: 'notice', requires_ack: true, acknowledged_at: null,
    audience_text: 'home-zone exposure: coverage unknown', message: 'Old internal status text'},
  message_lines: [
    'USDA demo office, Brantley GA: closure, Tuesday. NWS Flood Warning until 4:30 AM EDT.',
    '**You are emergency staff for animal care, so still report on site at 05:00.**',
    'Building access: open to you.', 'Please confirm: https://demo.invalid/ack/notice',
    'Demo: synthetic office, real NWS alert',
  ],
} as unknown as MyStatus;

it('shows the hazard and bold personal instruction, without internal status lines', async () => {
  vi.spyOn(api, 'me').mockResolvedValue(status);
  vi.spyOn(api, 'ack').mockResolvedValue({} as Notification);
  render(<MyStatusPage />);
  expect(await screen.findByText(/NWS Flood Warning until/)).toBeTruthy();
  expect(screen.getByText(/still report on site at 05:00/).tagName).toBe('STRONG');
  expect(screen.queryByText(/home-zone|Old internal/)).toBeNull();
  expect(screen.queryByText(/https:\/\/demo.invalid/)).toBeNull();
  expect(screen.getAllByRole('button')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button'));
  expect(api.ack).toHaveBeenCalledWith('notice');
});

it('FYI says no reply needed and has no acknowledgment control', async () => {
  vi.spyOn(api, 'me').mockResolvedValue({...status, notification: null,
    message_lines: [status.message_lines[0], '**Don’t come in; work from home from 07:30.**',
      'No reply needed.', status.message_lines[4]]});
  render(<MyStatusPage />);
  expect(await screen.findByText('No reply needed.')).toBeTruthy();
  expect(screen.queryByRole('button')).toBeNull();
});

it('Spanish instructions and acknowledgment use the same structure', async () => {
  vi.spyOn(api, 'me').mockResolvedValue({...status,
    employee: {...status.employee, language: 'es'},
    message_lines: ['USDA demo office, Brantley GA: cierre. NWS: Aviso de inundación hasta 4:30 AM.',
      '**Es personal de emergencia; preséntese a las 05:00.**', 'Acceso al edificio: abierto para usted.',
      'Confirme: https://demo.invalid/ack/notice', 'Demo: oficina sintética, alerta real del NWS']});
  render(<MyStatusPage />);
  expect(await screen.findByText(/Aviso de inundación/)).toBeTruthy();
  expect(screen.getByRole('button').textContent).toBe('Confirmar recepción');
});
