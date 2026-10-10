import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import DecidePage from "./pages/DecidePage";
import FacilityPage from "./pages/FacilityPage";
import NationalPage from "./pages/NationalPage";
import RecordPage from "./pages/RecordPage";

const NAV = [
  { to: "/facility", label: "Facility", hint: "Recommendation" },
  { to: "/decide", label: "Decide", hint: "Approve & notify" },
  { to: "/national", label: "National scan", hint: "Active US alerts" },
  { to: "/record", label: "Record", hint: "Replay decisions" },
];

export default function App() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <nav className="bg-slate-900 text-slate-100 md:w-56 md:shrink-0">
        <div className="px-4 py-4">
          <div className="text-lg font-extrabold tracking-tight">Closure Call</div>
          <div className="text-xs text-slate-400">Weather operating-posture support</div>
        </div>
        <ul className="flex overflow-x-auto md:block">
          {NAV.map((n) => (
            <li key={n.to} className="shrink-0">
              <NavLink
                to={n.to}
                className={({ isActive }) =>
                  `block px-4 py-3 md:border-l-4 ${isActive ? "bg-slate-800 md:border-yellow-400 font-semibold" : "md:border-transparent hover:bg-slate-800"}`
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
        <Routes>
          <Route path="/" element={<Navigate to="/facility" replace />} />
          <Route path="/facility" element={<FacilityPage />} />
          <Route path="/decide" element={<DecidePage />} />
          <Route path="/national" element={<NationalPage />} />
          <Route path="/record" element={<RecordPage />} />
          <Route path="*" element={<Navigate to="/facility" replace />} />
        </Routes>
      </main>
    </div>
  );
}
