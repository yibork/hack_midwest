import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, type RecommendationDetail } from '../api';
import InvestigationPanel from './InvestigationPanel';

vi.mock('./AgentActivity', () => ({default: () => <div>Live tool steps</div>}));
const rec = {id: 'rec', operating_date: '2026-10-13', status: 'final'} as RecommendationDetail;
beforeEach(() => {
  vi.spyOn(api, 'suggestions').mockResolvedValue({suggestions: [{action: 'explain_confidence', label: 'Why this confidence?'}], budget: {used: 3, limit: 12, label: '3 of 12 checks used'}});
  vi.spyOn(api, 'investigations').mockResolvedValue([]);
  vi.spyOn(api, 'health').mockResolvedValue({demo_mode: false} as Awaited<ReturnType<typeof api.health>>);
  vi.spyOn(api, 'staffCandidates').mockResolvedValue([{id: 'ops', name: 'Site Operator', role: 'facility_ops'}]);
  vi.spyOn(api, 'investigate').mockResolvedValue({investigation_id: 'inv'});
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('shows target weekday and budget; sends record explanation action', async () => {
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  expect(screen.getByPlaceholderText("Ask about Tuesday's conditions…")).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Why this confidence?'}));
  await waitFor(() => expect(api.investigate).toHaveBeenCalledWith('rec', {action: 'explain_confidence'}));
});

it('sends the free-text question and staff request to the chosen persona', async () => {
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.change(screen.getByLabelText('Investigation question'), {target: {value: 'Is the access road safe?'}});
  fireEvent.click(screen.getByRole('button', {name: 'Ask'}));
  await waitFor(() => expect(api.investigate).toHaveBeenCalledWith('rec', {question: 'Is the access road safe?'}));
  fireEvent.click(screen.getByRole('button', {name: 'Ask staff on site'}));
  fireEvent.change(screen.getByLabelText('Staff recipient'), {target: {value: 'ops'}});
  fireEvent.click(screen.getByRole('button', {name: 'Send evidence request'}));
  await waitFor(() => expect(api.investigate).toHaveBeenCalledWith('rec', expect.objectContaining({action: 'ask_staff', employee_id: 'ops', options: ['Unsafe', 'Safe', 'Unsure']})));
});

it('disables new questions while a recommendation is preliminary', async () => {
  render(<MemoryRouter><InvestigationPanel rec={{...rec, status: 'preliminary'}} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.change(screen.getByLabelText('Investigation question'), {target: {value: 'Check roads'}});
  expect((screen.getByRole('button', {name: 'Ask'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', {name: 'Why this confidence?'}) as HTMLButtonElement).disabled).toBe(true);
});
