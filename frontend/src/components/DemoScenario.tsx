import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type RecommendationDetail, type NationalAlert } from "../api";
import { Button, ErrorBox, Loading, useAsync } from "./ui";

export function ScenarioBanner() {
  const { pathname } = useLocation();
  const current = useAsync(() => api.demoState(), [pathname]);
  useEffect(() => {
    const reload = () => { void current.reload(); };
    window.addEventListener("demo-scenario-change", reload);
    return () => window.removeEventListener("demo-scenario-change", reload);
  }, [current.reload]);
  return <div className="scenario-banner mb-4 border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
    <strong>DEMO · Synthetic facility and roster.</strong> {current.data?.label || "Live: KC facility, next operating day"}
    {current.data?.captured_at && <span> · Original capture: {new Date(current.data.captured_at).toLocaleString()}</span>}
  </div>;
}

export default function DemoScenario({ onChange }: {onChange: (rec: RecommendationDetail | null) => void}) {
  const current = useAsync(() => api.demoState(), []);
  const recordings = useAsync(async () => (await api.demoState()).enabled ? api.demoRecordings() : [], []);
  const [mode, setMode] = useState("live_national");
  const [alerts, setAlerts] = useState<NationalAlert[] | null>(null);
  const [selectedAlert, setSelectedAlert] = useState("");
  const [scanAt, setScanAt] = useState<string | null>(null);
  const [recording, setRecording] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  async function scan() {
    setBusy(true); setError(null); setSelectedAlert("");
    try {
      const result = await api.demoNationalAlerts();
      if (result.status !== 'ok') throw new Error('NWS national scan is unavailable. No mock alerts have been substituted.');
      setAlerts(result.alerts); setScanAt(result.retrieved_at);
    } catch(e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  async function change(reset = false) {
    setBusy(true); setError(null);
    try {
      if (reset) { await api.demoReset(); onChange(null); setMode("live_national"); setAlerts(null); setSelectedAlert(''); setConfirm(false); }
      else onChange(mode === 'live_national' ? await api.demoScenario(mode, undefined, selectedAlert) : await api.demoScenario(mode, recording));
      await current.reload(); await recordings.reload();
      window.dispatchEvent(new Event("demo-scenario-change"));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  if (!current.data?.enabled) return null;
  return <section className="rounded border border-amber-200 bg-white p-3 space-y-2" aria-label="Demo controls">
    <div className="flex flex-wrap gap-2 items-end">
      <label className="text-sm">Demo scenario
        <select className="block border p-2" value={mode} onChange={e => setMode(e.target.value)} disabled={busy}>
          <option value="live_next">Live: KC facility, next operating day</option>
          <option value="live_today">Live: KC facility, today as a workday (demo)</option>
          <option value="live_national">Live: most severe active US alert</option>
          <option value="replay">Replay: recorded moment</option>
          <option value="simulated_winter">Simulated: winter storm</option>
        </select>
      </label>
      {mode === "replay" && <label className="text-sm">Recorded moment<select className="block border p-2" value={recording}
        onChange={e => setRecording(e.target.value)}><option value="">Choose a recording</option>
        {recordings.data?.map(r => <option value={r.id} key={r.id}>{r.label}</option>)}</select></label>}
      {mode === 'live_national' ? <>
        <Button disabled={busy} variant="secondary" onClick={scan}>Scan real alerts</Button>
        <Button disabled={busy || !selectedAlert} onClick={() => change()}>Relocate demo facility here (demo)</Button>
      </> : <Button disabled={busy || (mode === "replay" && !recording)} onClick={() => change()}>Load scenario</Button>}
      <Button variant="secondary" disabled={busy} onClick={() => setConfirm(true)}>Reset demo</Button>
    </div>
    {mode === 'live_national' && <div className="space-y-2">
      <p className="text-sm">Main demo path: real NWS alerts; synthetic facility and unchanged demo phones. Moderate or higher, ongoing or starting within 24 hours. Ranked by severity, urgency, then certainty.</p>
      {scanAt && <p className="text-sm text-slate-600">Live scan: {new Date(scanAt).toLocaleString()}. Choose one of the top five; no alert is selected automatically.</p>}
      {alerts?.length === 0 && <p role="status">No qualifying real alerts are available. Try again later; no simulated feed is substituted.</p>}
      {alerts?.map(a => <label key={a.id} className="block border border-slate-200 p-3">
        <input type="radio" name="national-alert" value={a.id} checked={selectedAlert === a.id}
          disabled={busy || a.lat == null || a.lon == null} onChange={() => setSelectedAlert(a.id)} />{' '}
        <strong>{a.event}</strong> · {a.area_desc ?? 'Place not stated'}
        <span className="block text-sm">{a.severity} · {a.urgency} · {a.certainty} · Expires {a.ends ? new Date(a.ends).toLocaleString('en-US', {timeZone: 'UTC'}) + ' UTC' : 'not stated'}{a.lat == null ? ' · Location unavailable' : ''}</span>
      </label>)}
    </div>}
    <p className="text-sm">Active: {current.data.label}. Nothing is sent until the manager approves and sends.</p>
    {busy && <Loading label="Loading scenario and evaluating the rules…" />}
    {confirm && <div role="alertdialog" aria-label="Confirm demo reset" className="border border-amber-300 p-3">
      <p>Clear active demo decisions, notices, investigations and timers? Historical memos, evidence, saved copies and the record will be kept.</p>
      <div className="flex gap-2 mt-2"><Button variant="danger" disabled={busy} onClick={() => change(true)}>Confirm reset</Button>
        <Button variant="secondary" onClick={() => setConfirm(false)}>Cancel</Button></div>
    </div>}
    {error && <ErrorBox error={error} />}
  </section>;
}
