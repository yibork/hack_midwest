import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Persona } from "../api";
import { Button, Card, ErrorBox, Loading, useAsync } from "../components/ui";

function Qr({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(url, { margin: 1, width: 180 }).then(setSrc).catch(() => setSrc(null));
  }, [url]);
  return src ? <img src={src} width={180} height={180} alt={`QR code for ${url}`} /> : <div className="h-[180px] w-[180px] bg-slate-100" />;
}

function PersonaCard({ p }: { p: Persona }) {
  const url = p.ntfy_topic ? `https://ntfy.sh/${p.ntfy_topic}` : null;
  const delivery = useAsync(async () => (await api.demoState()).enabled ? api.demoDeliveryStatus() : [], []);
  const last = delivery.data?.find(row => row.employee_id === p.employee_id)?.last_delivery;
  return (
    <Card title={p.name}>
      <div className="flex flex-col items-start gap-3 sm:flex-row">
        {url && <Qr url={url} />}
        <div className="space-y-2 text-sm">
          <p>{p.description}</p>
          <p>Role: {p.role.replaceAll("_", " ")}</p>
          <p aria-live="polite">Last phone delivery: {last ? `${last.ok ? "Delivered" : "Failed"} · ${last.kind} · ${new Date(last.at).toLocaleString()}${last.error ? ` — ${last.error}` : ""}` : "No push delivery yet"}</p>
          <Button variant="secondary" onClick={delivery.reload}>Refresh delivery status</Button>
          {url && <p>Scan to subscribe in the ntfy app, or open <a className="break-all underline" href={url} target="_blank" rel="noreferrer">{url}</a>.</p>}
          <p className="font-mono text-xs text-slate-500">topic: {p.ntfy_topic}</p>
        </div>
      </div>
    </Card>
  );
}

/** Synthetic demo personas, phone subscriptions and last delivery results. */
export default function PersonasPage() {
  const viewers = useAsync(() => api.viewers(), []);

  if (viewers.loading) return <Loading />;
  if (viewers.error) return <ErrorBox error={viewers.error} onRetry={viewers.reload} />;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Demo personas</h1>
      <p className="text-sm text-slate-600">
        Push messages go to public ntfy.sh topics; these are synthetic people. The "already teleworking" persona gets
        email only, so its topic stays quiet on purpose.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {viewers.data?.personas.map((p) => <PersonaCard key={p.employee_id} p={p} />)}
      </div>
      <p className="text-sm"><Link className="underline" to="/facility">Load the winter-storm scenario or reset the demo on the Facility page.</Link> Scenario evidence stays separate from live evaluations.</p>
    </div>
  );
}
