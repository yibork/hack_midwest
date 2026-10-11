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
  // A second request is permitted only after the first finishes; use a fresh panel here.
  cleanup();
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.click(screen.getByRole('button', {name: 'Ask staff on site'}));
  fireEvent.change(screen.getByLabelText('Staff recipient'), {target: {value: 'ops'}});
  fireEvent.click(screen.getByRole('button', {name: 'Send evidence request'}));
  await waitFor(() => expect(api.investigate).toHaveBeenCalledWith('rec', expect.objectContaining({action: 'ask_staff', employee_id: 'ops', options: ['Unsafe', 'Safe', 'Unsure']})));
});

it('shows immediate submission feedback and live steps through completion', async () => {
  let accept!: (r: {investigation_id: string}) => void;
  vi.mocked(api.investigate).mockImplementation(() => new Promise(resolve => {accept = resolve;}));
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.click(screen.getByRole('button', {name: 'Why this confidence?'}));
  expect(screen.getByText('Submitting investigation…')).toBeTruthy();
  accept({investigation_id: 'inv'});
  await screen.findByText('Queued — waiting to start');
  expect(screen.getByText('Live tool steps')).toBeTruthy();
  vi.mocked(api.investigations).mockResolvedValue([{
    id: 'inv', recommendation_id: 'rec', question: 'explain confidence', action: 'explain_confidence', status: 'completed',
    created_at: '2026-10-11T00:00:00Z', revised_recommendation_id: null,
    budget: {used: 3, limit: 12, label: '3 of 12 checks used'},
    result: {answer: 'For Tuesday, confidence is low.', source_label: 'Record summary'}, staff_questions: [],
  }]);
  await screen.findByText('For Tuesday, confidence is low.', {}, {timeout: 3000});
  expect(screen.queryByText('Queued — waiting to start')).toBeNull();
  expect(screen.getByText('Live tool steps')).toBeTruthy();
});

it('shows request failures instead of silently waiting', async () => {
  vi.mocked(api.investigate).mockRejectedValue(new Error('Network request timed out'));
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.click(screen.getByRole('button', {name: 'Why this confidence?'}));
  await screen.findByText('Network request timed out');
  expect(screen.queryByText('Submitting investigation…')).toBeNull();
});

it('surfaces revision-loading failures and retries instead of getting stuck on preliminary', async () => {
  const onRevision = vi.fn().mockRejectedValueOnce(new Error('temporary fetch failure')).mockResolvedValue(undefined);
  vi.mocked(api.investigations).mockResolvedValue([{
    id: 'inv', recommendation_id: 'rec', question: 'Check access', action: 'check_access_routes', status: 'completed',
    created_at: '2026-10-11T00:00:00Z', revised_recommendation_id: 'revision',
    budget: {used: 0, limit: 12, label: '0 of 12 checks used'}, result: {new_evidence_ids: ['ev']}, staff_questions: [],
  }]);
  render(<MemoryRouter><InvestigationPanel rec={rec} onRevision={onRevision} /></MemoryRouter>);
  await screen.findByText(/Unable to load investigation progress: temporary fetch failure/);
  await waitFor(() => expect(onRevision).toHaveBeenCalledTimes(2), {timeout: 3000});
  await waitFor(() => expect(screen.queryByText(/Unable to load investigation progress/)).toBeNull());
});

it('disables new questions while a recommendation is preliminary', async () => {
  render(<MemoryRouter><InvestigationPanel rec={{...rec, status: 'preliminary'}} onRevision={() => {}} /></MemoryRouter>);
  await screen.findByText('3 of 12 checks used');
  fireEvent.change(screen.getByLabelText('Investigation question'), {target: {value: 'Check roads'}});
  expect((screen.getByRole('button', {name: 'Ask'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', {name: 'Why this confidence?'}) as HTMLButtonElement).disabled).toBe(true);
});
