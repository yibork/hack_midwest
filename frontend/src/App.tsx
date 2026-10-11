import closureCallLogo from "./assets/closure-call-logo.png";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { RoleProvider, useViewer, type ViewerRole } from "./components/RoleContext";
import RoleSwitcher from "./components/RoleSwitcher";
import DecidePage from "./pages/DecidePage";
import FacilityPage from "./pages/FacilityPage";
import MyStatusPage from "./pages/MyStatusPage";
import NationalPage from "./pages/NationalPage";
import PersonasPage from "./pages/PersonasPage";
import RecordPage from "./pages/RecordPage";
import SecurityPage from "./pages/SecurityPage";
import SourcesPage from "./pages/SourcesPage";
import TeamPage from "./pages/TeamPage";
import ThresholdsPage from "./pages/ThresholdsPage";
import { ScenarioBanner } from "./components/DemoScenario";
import AcknowledgePage from "./pages/AcknowledgePage";
import SavedCopyPage from "./pages/SavedCopyPage";
import SavedEvidencePage from './pages/SavedEvidencePage';

const NAV: Record<ViewerRole, { to: string; label: string; hint: string }[]> = {
  facility_manager: [
    { to: "/facility", label: "Facility", hint: "Recommendation" },
    { to: "/decide", label: "Decide", hint: "Approve, notify, call-ins" },
    { to: "/team", label: "Teams", hint: "Supervisor boards" },
    { to: "/security", label: "Access list", hint: "Security desk view" },
    { to: "/sources", label: "Sources", hint: "Local signals allowlist" },
    { to: "/thresholds", label: "Thresholds", hint: "Policy and verified citations" },
    { to: "/personas", label: "Demo personas", hint: "ntfy QR codes" },
    { to: "/national", label: "National scan", hint: "Active US alerts" },
    { to: "/record", label: "Record", hint: "Replay decisions" },
  ],
  supervisor: [{ to: "/team", label: "My team", hint: "Status, acks, exposure" }],
  security_desk: [{ to: "/security", label: "Access list", hint: "Who may enter today" }],
  employee: [{ to: "/me", label: "My status", hint: "What to do today" }],
  auditor: [{to: '/record', label: 'Record', hint: 'Verified, read-only audit'}, {to: '/thresholds', label: 'Thresholds', hint: 'Policy and citations'}],
};

function Shell() {
  const { viewer, ready } = useViewer();
  const location = useLocation();
  const fixed = new URLSearchParams(location.search).has('role');
  const query = new URLSearchParams({role: viewer.role, ...(viewer.personId ? {person: viewer.personId} : {})});
  const fixedQuery = fixed ? `?${query}` : '';
  const nav = NAV[viewer.role];
  const home = nav[0].to;
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <nav className="operations-sidebar md:shrink-0" aria-label="Primary navigation">
        <div className="px-4 py-4">
          <img
            src={closureCallLogo}
            alt="Closure Call"
            width={2172}
            height={724}
            className="mb-2 block h-auto w-52 max-w-full md:w-full"
          />
          <div className="text-xs text-slate-600">Facility operations support</div>
        </div>
        <div className="px-4 pb-3"><RoleSwitcher /></div>
        <ul className="flex overflow-x-auto md:block">
          {nav.map((n) => (
            <li key={n.to} className="shrink-0">
              <NavLink
                to={`${n.to}${fixedQuery}`}
                className={({ isActive }) =>
                  `block px-4 py-2 border-l-4 ${isActive ? "active font-semibold" : "border-transparent"}`
                }
              >
                <div>{n.label}</div>
                <div className="hidden text-xs text-slate-400 md:block">{n.hint}</div>
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="hidden px-4 py-6 text-xs text-slate-500 md:block">
          Rules recommend. Humans decide. Nothing is sent without an approval record.
        </p>
      </nav>
      <main className="min-w-0 flex-1 p-4 md:p-6">
        <ScenarioBanner />
        {viewer.role === 'auditor' && <p className="mb-4 rounded border border-slate-300 bg-white p-3 font-semibold">Read-only · Auditor — no operational actions available</p>}
        {!ready ? (
          <div role="status" className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
            Choose who you're viewing as in the menu.
          </div>
        ) : (
        <Routes key={`${viewer.role}:${viewer.personId ?? ""}`}>
          <Route path="/" element={<Navigate to={home} replace />} />
          {viewer.role === "facility_manager" && (
            <>
              <Route path="/facility" element={<FacilityPage />} />
              <Route path="/decide" element={<DecidePage />} />
              <Route path="/sources" element={<SourcesPage />} />
              <Route path="/thresholds" element={<ThresholdsPage />} />
              <Route path="/personas" element={<PersonasPage />} />
              <Route path="/national" element={<NationalPage />} />
              <Route path="/record" element={<RecordPage />} />
            </>
          )}
          {(viewer.role === "facility_manager" || viewer.role === "supervisor") && <Route path="/team" element={<TeamPage />} />}
          {viewer.role === 'auditor' && <><Route path="/record" element={<RecordPage />} /><Route path="/thresholds" element={<ThresholdsPage />} /></>}
          {(viewer.role === "facility_manager" || viewer.role === "security_desk") && (
            <Route path="/security" element={<SecurityPage />} />
          )}
          {viewer.role === "employee" && <Route path="/me" element={<MyStatusPage />} />}
          <Route path="*" element={<Navigate to={`${home}${fixedQuery}`} replace />} />
        </Routes>
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/acknowledge/:id" element={<AcknowledgePage />} />
      <Route path="/saved-copy/:digest" element={<RoleProvider><SavedCopyPage /></RoleProvider>} />
      <Route path="/saved-evidence/:id" element={<RoleProvider><SavedEvidencePage /></RoleProvider>} />
      <Route path="*" element={<RoleProvider><Shell /></RoleProvider>} />
    </Routes>
  );
}
