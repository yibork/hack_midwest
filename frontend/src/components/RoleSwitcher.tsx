import { useEffect, useState } from "react";
import { NEEDS_PERSON_EVENT } from "../api";
import { NEEDS_PERSON, peopleFor, useViewer, type ViewerRole } from "./RoleContext";

const ROLE_LABEL: Record<ViewerRole, string> = {
  facility_manager: "Facility manager",
  supervisor: "Supervisor",
  security_desk: "Security desk",
  employee: "Employee",
};

const words = (s: string) => s.replace(/_/g, " ");

/** Demo only: pick who you are. The server filters every response by this role and person. */
export default function RoleSwitcher() {
  const { viewer, viewers, setRole, setPerson } = useViewer();
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    const onNeed = () => setAsked(true);
    window.addEventListener(NEEDS_PERSON_EVENT, onNeed);
    return () => window.removeEventListener(NEEDS_PERSON_EVENT, onNeed);
  }, []);
  useEffect(() => setAsked(false), [viewer.personId, viewer.role]);

  const needsPerson = NEEDS_PERSON.has(viewer.role);
  const { personas, others } = peopleFor(viewer.role, viewers);
  const showPrompt = needsPerson && (asked || !viewer.personId);

  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="rounded bg-yellow-400 px-1.5 py-0.5 text-xs font-bold text-slate-900">DEMO</span>
        <label className="text-xs text-slate-400" htmlFor="role">Role</label>
      </div>
      <select id="role" className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
              value={viewer.role} onChange={(e) => setRole(e.target.value as ViewerRole)}>
        {(Object.keys(ROLE_LABEL) as ViewerRole[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
      </select>
      {needsPerson && (
        <div>
          <label className="block text-xs text-slate-400" htmlFor="person">Viewing as</label>
          <select id="person" className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
                  value={viewer.personId ?? ""} onChange={(e) => setPerson(e.target.value)}>
            {!viewer.personId && <option value="" disabled>Choose a person…</option>}
            {personas.length > 0 && (
              <optgroup label="Demo personas">
                {personas.map((p) => <option key={p.id} value={p.id}>★ {p.name} · {words(p.role)} · {words(p.designation)}</option>)}
              </optgroup>
            )}
            <optgroup label={viewer.role === "supervisor" ? "Supervisors" : "Everyone"}>
              {others.map((p) => <option key={p.id} value={p.id}>{p.name} · {words(p.role)} · {words(p.designation)}</option>)}
            </optgroup>
          </select>
          {showPrompt && (
            <p role="status" className="mt-1 rounded bg-yellow-100 px-2 py-1 text-xs font-semibold text-slate-900">
              Choose who you're viewing as
            </p>
          )}
        </div>
      )}
    </div>
  );
}
