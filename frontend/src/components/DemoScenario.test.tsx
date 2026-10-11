import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type RecommendationDetail } from "../api";
import DemoScenario from "./DemoScenario";

beforeEach(() => {
  vi.spyOn(api, "demoState").mockResolvedValue({enabled: true, mode: "live_next", label: "Live: KC facility, next operating day", recommendation_id: null, facility_id: "usda-kc-demo", captured_at: null});
  vi.spyOn(api, "demoRecordings").mockResolvedValue([]);
  vi.spyOn(api, "demoReset").mockResolvedValue({recordings_retained: true});
  vi.spyOn(api, "demoScenario").mockResolvedValue({id: "storm"} as RecommendationDetail);
  vi.spyOn(api, "demoNationalAlerts").mockResolvedValue({alerts: [], retrieved_at: '2026-10-11T05:00:00Z', status: 'ok', snapshot_hash: 'hash', note: 'Live'});
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it("requires confirmation before resetting and keeps historical records", async () => {
  const change = vi.fn();
  render(<DemoScenario onChange={change} />);
  fireEvent.click(await screen.findByText("Reset demo"));
  expect(api.demoReset).not.toHaveBeenCalled();
  expect(screen.getByRole("alertdialog").textContent).toContain("Historical memos, evidence, saved copies and the record will be kept");
  fireEvent.click(screen.getByText("Confirm reset"));
  await waitFor(() => expect(change).toHaveBeenCalledWith(null));
  expect(api.demoReset).toHaveBeenCalledTimes(1);
});

it("loads the clearly labelled winter scenario", async () => {
  const change = vi.fn();
  render(<DemoScenario onChange={change} />);
  fireEvent.change(await screen.findByLabelText("Demo scenario"), {target: {value: "simulated_winter"}});
  fireEvent.click(screen.getByText("Load scenario"));
  await waitFor(() => expect(api.demoScenario).toHaveBeenCalledWith("simulated_winter", ""));
  expect(change).toHaveBeenCalledWith({id: "storm"});
});

it("has no reset or scenario controls outside DEMO_MODE", async () => {
  vi.mocked(api.demoState).mockResolvedValue({enabled: false, mode: "live_next", label: "Live", recommendation_id: null, facility_id: "office", captured_at: null});
  const {container} = render(<DemoScenario onChange={vi.fn()} />);
  await waitFor(() => expect(api.demoState).toHaveBeenCalled());
  expect(container.textContent).toBe("");
});

it('defaults to real alerts and requires a presenter choice before relocation', async () => {
  vi.mocked(api.demoNationalAlerts).mockResolvedValue({alerts: [{id: 'real-alert', event: 'High Wind Warning',
    severity: 'Severe', urgency: 'Immediate', certainty: 'Observed', onset: '2026-10-11T05:00:00Z',
    ends: '2026-10-11T18:00:00Z', headline: 'Wind warning', area_desc: 'Boulder, CO', sent: null, lat: 40, lon: -105}],
    retrieved_at: '2026-10-11T05:00:00Z', status: 'ok', snapshot_hash: 'hash', note: 'Live'});
  render(<DemoScenario onChange={vi.fn()} />);
  await screen.findByText('Scan real alerts');
  expect((screen.getByLabelText('Demo scenario') as HTMLSelectElement).value).toBe('live_national');
  expect((screen.getByText('Relocate demo facility here (demo)') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('Scan real alerts'));
  fireEvent.click(await screen.findByRole('radio'));
  expect(screen.getByText(/Expires.*UTC/)).toBeTruthy();
  fireEvent.click(screen.getByText('Relocate demo facility here (demo)'));
  await waitFor(() => expect(api.demoScenario).toHaveBeenCalledWith('live_national', undefined, 'real-alert'));
});

it('never substitutes a mock feed when there are no qualifying live alerts', async () => {
  render(<DemoScenario onChange={vi.fn()} />);
  fireEvent.click(await screen.findByText('Scan real alerts'));
  expect(await screen.findByText(/No qualifying real alerts/)).toBeTruthy();
  expect(api.demoScenario).not.toHaveBeenCalled();
});
