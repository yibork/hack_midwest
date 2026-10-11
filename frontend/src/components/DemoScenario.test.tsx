import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type RecommendationDetail } from "../api";
import DemoScenario from "./DemoScenario";

beforeEach(() => {
  vi.spyOn(api, "demoState").mockResolvedValue({enabled: true, mode: "live_next", label: "Live: KC facility, next operating day", recommendation_id: null, facility_id: "usda-kc-demo", captured_at: null});
  vi.spyOn(api, "demoRecordings").mockResolvedValue([]);
  vi.spyOn(api, "demoReset").mockResolvedValue({recordings_retained: true});
  vi.spyOn(api, "demoScenario").mockResolvedValue({id: "storm"} as RecommendationDetail);
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
