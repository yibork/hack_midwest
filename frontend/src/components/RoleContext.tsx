import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setViewer as setApiViewer, type EmployeePublic, type Persona, type Viewers } from "../api";

export type ViewerRole = "facility_manager" | "supervisor" | "security_desk" | "employee";
export interface Viewer {
  role: ViewerRole;
  personId: string | null;
}

export const NEEDS_PERSON: ReadonlySet<ViewerRole> = new Set(["employee", "supervisor"]);
const KEY = "closurecall.viewer";
const DEFAULT: Viewer = { role: "facility_manager", personId: null };
const ROLES: ViewerRole[] = ["facility_manager", "supervisor", "security_desk", "employee"];

function load(): Viewer {
  // /me?as=<employee id> (the link on the ack page) opens My status as that person.
  try {
    const as = new URLSearchParams(window.location.search).get("as");
    if (as) return { role: "employee", personId: as };
  } catch {
    /* no URL: ignore */
  }
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && ROLES.includes(v.role)) return { role: v.role, personId: typeof v.personId === "string" ? v.personId : null };
  } catch {
    /* storage unavailable or corrupt: use the default */
  }
  return DEFAULT;
}

function save(v: Viewer) {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    /* per-viewer convenience only */
  }
}

/** Who can be picked for a role: supervisors only, or every employee with the demo personas first. */
export function peopleFor(role: ViewerRole, viewers: Viewers | null): { personas: (EmployeePublic & { persona?: Persona })[]; others: EmployeePublic[] } {
  if (!viewers) return { personas: [], others: [] };
  if (role === "supervisor") return { personas: [], others: viewers.supervisors.filter((p) => p.role === "supervisor") };
  if (role !== "employee") return { personas: [], others: [] };
  const byId = new Map(viewers.employees.map((e) => [e.id, e]));
  const personas = viewers.personas.flatMap((p) => (byId.has(p.employee_id) ? [{ ...byId.get(p.employee_id)!, persona: p }] : []));
  const pinned = new Set(personas.map((p) => p.id));
  return { personas, others: viewers.employees.filter((e) => !pinned.has(e.id)) };
}

/** Default person for a role: the first demo persona (employee) or the first supervisor. */
export function defaultPerson(role: ViewerRole, viewers: Viewers | null): string | null {
  const { personas, others } = peopleFor(role, viewers);
  return personas[0]?.id ?? others[0]?.id ?? null;
}

function valid(v: Viewer, viewers: Viewers): boolean {
  if (!NEEDS_PERSON.has(v.role)) return true;
  const { personas, others } = peopleFor(v.role, viewers);
  return [...personas, ...others].some((p) => p.id === v.personId);
}

interface Ctx {
  viewer: Viewer;
  viewers: Viewers | null;
  /** False while the person list loads, or if a role that needs a person has none. */
  ready: boolean;
  setRole: (role: ViewerRole) => void;
  setPerson: (personId: string) => void;
}

const RoleCtx = createContext<Ctx>({ viewer: DEFAULT, viewers: null, ready: true, setRole: () => {}, setPerson: () => {} });

/** Demo role switcher state (no real auth). Every API call carries X-Role, and X-Person when set. */
export function RoleProvider({ children }: { children: ReactNode }) {
  const [viewer, setState] = useState<Viewer>(() => {
    const v = load();
    setApiViewer(v.role, v.personId);
    save(v); // e.g. the ack page's "View my status" link: remember who we're viewing as
    return v;
  });
  const [viewers, setViewers] = useState<Viewers | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const apply = useCallback((v: Viewer) => {
    setApiViewer(v.role, v.personId); // before children re-render and fetch
    save(v);
    setState(v);
  }, []);

  useEffect(() => {
    api.viewers().then(setViewers).catch(() => setLoadFailed(true));
  }, []);

  // Never leave a role that needs a person without a valid one.
  useEffect(() => {
    if (viewers && !valid(viewer, viewers)) apply({ role: viewer.role, personId: defaultPerson(viewer.role, viewers) });
  }, [viewers, viewer, apply]);

  const value = useMemo<Ctx>(() => ({
    viewer,
    viewers,
    ready: !NEEDS_PERSON.has(viewer.role) || (!!viewer.personId && (loadFailed || (!!viewers && valid(viewer, viewers)))),
    setRole: (role) => apply({ role, personId: NEEDS_PERSON.has(role) ? defaultPerson(role, viewers) : null }),
    setPerson: (personId) => apply({ ...viewer, personId }),
  }), [viewer, viewers, loadFailed, apply]);

  return <RoleCtx.Provider value={value}>{children}</RoleCtx.Provider>;
}

export const useViewer = () => useContext(RoleCtx);
