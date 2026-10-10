/**
 * The demo role switcher must never land on "X-Person ... is required": switching to Employee or
 * Supervisor picks a person (first demo persona / first supervisor), and every request carries
 * X-Person. The fake server below enforces the same header rule as backend/app/roles.py.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const person = (id: string, name: string, role: string, designation = "telework_ready") => ({
  id, name, role, designation, supervisor_id: null, functions: [], primary_function: null,
  accommodation_on_file: false, language: "en", preferred_channels: ["email"], commute_minutes: 20, access_level: "standard",
});
const PEOPLE = [
  person("emp-001", "Morgan Ellery", "facility_manager", "emergency"),
  person("emp-002", "Dana Reyes", "supervisor", "emergency"),
  person("emp-003", "Kwame Asante", "supervisor"),
  person("emp-005", "Ava Brennan", "employee", "emergency"),
  person("emp-031", "Sam Whitaker", "employee"),
  person("emp-040", "Carla Mendes", "employee"),
];
const VIEWERS = {
  supervisors: PEOPLE.filter((p) => p.role === "supervisor" || p.role === "facility_manager"),
  security_desk: [],
  employees: PEOPLE,
  personas: [
    { employee_id: "emp-005", name: "Ava Brennan", role: "employee", ntfy_topic: "closurecall-demo-emergency", description: "" },
    { employee_id: "emp-031", name: "Sam Whitaker", role: "employee", ntfy_topic: "closurecall-demo-telework", description: "" },
  ],
};

const requests: { path: string; headers: Record<string, string> }[] = [];

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function fakeServer(input: RequestInfo | URL, init?: RequestInit) {
  const url = new URL(String(input), "http://test");
  const headers = (init?.headers ?? {}) as Record<string, string>;
  requests.push({ path: url.pathname, headers });
  const role = headers["X-Role"] ?? "facility_manager";
  const who = PEOPLE.find((p) => p.id === headers["X-Person"]);
  if (url.pathname === "/api/viewers") return json(VIEWERS);
  if ((role === "employee" || role === "supervisor") && !who) {
    return json({ detail: "X-Person (an employee id) is required for this role" }, 400);
  }
  if (url.pathname === "/api/me") {
    return json({ employee: who, facility_name: "KC", operating_date: "2026-10-10", posture: null, notification: null,
                  duty_instruction: "no change: work your normal schedule", building_access: "allowed", ack_url: null });
  }
  if (url.pathname === "/api/team") {
    return json({ supervisor: who, notice: null, members: [], notifications: [], suggestions: [] });
  }
  if (url.pathname === "/api/facilities") return json([]);
  return json(null);
}

function renderApp(path = "/") {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

const selectRole = (role: string) => fireEvent.change(screen.getByLabelText("Role"), { target: { value: role } });
const noPersonError = () => expect(document.body.textContent).not.toMatch(/X-Person|required for this role|Error:/);

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() { return m.size; },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

beforeEach(() => {
  requests.length = 0;
  // Node >= 22 has its own localStorage global that shadows jsdom's; use a clean in-memory one.
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("fetch", vi.fn(fakeServer));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("demo role switcher", () => {
  it("switching to Employee picks the first demo persona and shows My status", async () => {
    renderApp();
    await waitFor(() => expect(requests.some((r) => r.path === "/api/viewers")).toBe(true));
    await act(async () => selectRole("employee"));
    await screen.findByText("Hello, Ava");
    expect((screen.getByLabelText("Viewing as") as HTMLSelectElement).value).toBe("emp-005");
    // personas pinned first, then everyone else, labelled with role and designation
    const labels = [...(screen.getByLabelText("Viewing as") as HTMLSelectElement).options].map((o) => o.textContent);
    expect(labels[0]).toBe("★ Ava Brennan · employee · emergency");
    expect(labels[1]).toBe("★ Sam Whitaker · employee · telework ready");
    noPersonError();
    expect(requests.filter((r) => r.path === "/api/me").every((r) => r.headers["X-Person"] === "emp-005")).toBe(true);
    expect(JSON.parse(localStorage.getItem("closurecall.viewer")!)).toEqual({ role: "employee", personId: "emp-005" });
  });

  it("switching to Supervisor lists only supervisors and shows the first one's team", async () => {
    renderApp();
    await waitFor(() => expect(requests.some((r) => r.path === "/api/viewers")).toBe(true));
    await act(async () => selectRole("supervisor"));
    await screen.findByText("Team · Dana Reyes");
    const names = [...(screen.getByLabelText("Viewing as") as HTMLSelectElement).options].map((o) => o.textContent);
    expect(names).toEqual(["Dana Reyes · supervisor · emergency", "Kwame Asante · supervisor · telework ready"]);
    noPersonError();
  });

  it("switching roles before the person list has loaded never shows the error", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
      if (String(i).endsWith("/api/viewers")) await gate;
      return fakeServer(i, init);
    }));
    renderApp();
    await act(async () => selectRole("employee"));
    noPersonError();
    expect(requests.some((r) => r.path === "/api/me")).toBe(false); // no request without a person
    await act(async () => release());
    await screen.findByText("Hello, Ava");
    noPersonError();
  });

  it("a stored employee role without a person recovers to the default persona", async () => {
    localStorage.setItem("closurecall.viewer", JSON.stringify({ role: "employee", personId: null }));
    renderApp("/me");
    await screen.findByText("Hello, Ava");
    noPersonError();
  });

  it("the ack page link /me?as=<id> opens My status as that recipient", async () => {
    window.history.pushState({}, "", "/me?as=emp-040");
    renderApp("/me?as=emp-040");
    await screen.findByText("Hello, Carla");
    expect(JSON.parse(localStorage.getItem("closurecall.viewer")!)).toEqual({ role: "employee", personId: "emp-040" });
    window.history.pushState({}, "", "/");
  });

  it("if the API still asks for a person, the UI asks politely instead of a red error", async () => {
    const { api, setViewer } = await import("./api");
    setViewer("employee", null);
    await expect(api.me()).rejects.toThrow("Choose who you're viewing as");
    const { ErrorBox } = await import("./components/ui");
    render(<ErrorBox error="Choose who you're viewing as" />);
    expect(screen.getByRole("status").textContent).toContain("Choose who you're viewing as");
    expect(document.body.textContent).not.toContain("Error:");
  });
});
