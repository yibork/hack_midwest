import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { setViewer } from "../api";

export type ViewerRole = "facility_manager" | "supervisor" | "security_desk" | "employee";
export interface Viewer {
  role: ViewerRole;
  personId: string | null;
}

const KEY = "closurecall.viewer";
const DEFAULT: Viewer = { role: "facility_manager", personId: null };

function load(): Viewer {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && typeof v.role === "string") return v;
  } catch {
    /* storage unavailable or corrupt: use the default */
  }
  return DEFAULT;
}

const Ctx = createContext<{ viewer: Viewer; setViewer: (v: Viewer) => void }>({ viewer: DEFAULT, setViewer: () => {} });

/** Demo role switcher state (no real auth). Every API call carries X-Role / X-Person. */
export function RoleProvider({ children }: { children: ReactNode }) {
  const [viewer, set] = useState<Viewer>(() => {
    const v = load();
    setViewer(v.role, v.personId);
    return v;
  });
  useEffect(() => {
    setViewer(viewer.role, viewer.personId);
    try {
      localStorage.setItem(KEY, JSON.stringify(viewer));
    } catch {
      /* per-viewer convenience only */
    }
  }, [viewer]);
  const update = (v: Viewer) => {
    setViewer(v.role, v.personId); // before children re-render and fetch
    set(v);
  };
  return <Ctx.Provider value={{ viewer, setViewer: update }}>{children}</Ctx.Provider>;
}

export const useViewer = () => useContext(Ctx);
