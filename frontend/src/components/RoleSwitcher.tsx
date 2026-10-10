import { useEffect, useState } from "react";
import { api, type Viewers } from "../api";
import { useViewer, type ViewerRole } from "./RoleContext";

const ROLE_LABEL: Record<ViewerRole, string> = {
  facility_manager: "Facility manager",
  supervisor: "Supervisor",
  security_desk: "Security desk",
  employee: "Employee",
};

/** Demo only: pick who you are. The server filters every response by this role. */
export default function RoleSwitcher() {
  const { viewer, setViewer } = useViewer();
  const [people, setPeople] = useState<Viewers | null>(null);
  useEffect(() => {
    api.viewers().then(setPeople).catch(() => setPeople(null));
  }, []);

  const options =
    viewer.role === "supervisor" ? people?.supervisors.filter((p) => p.role === "supervisor") ?? []
      : viewer.role === "employee" ? people?.employees ?? [] : [];

  function pickRole(role: ViewerRole) {
    const first = role === "supervisor" ? people?.supervisors.find((p) => p.role === "supervisor")
      : role === "employee" ? people?.personas[0] && { id: people.personas[0].employee_id } : null;
    setViewer({ role, personId: first ? first.id : null });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="rounded bg-yellow-400 px-1.5 py-0.5 text-xs font-bold text-slate-900">DEMO</span>
      <label className="sr-only" htmlFor="role">Role</label>
      <select id="role" className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
              value={viewer.role} onChange={(e) => pickRole(e.target.value as ViewerRole)}>
        {(Object.keys(ROLE_LABEL) as ViewerRole[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
      </select>
      {options.length > 0 && (
        <>
          <label className="sr-only" htmlFor="person">Person</label>
          <select id="person" className="max-w-[14rem] rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
                  value={viewer.personId ?? ""} onChange={(e) => setViewer({ ...viewer, personId: e.target.value })}>
            {options.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </>
      )}
    </div>
  );
}
