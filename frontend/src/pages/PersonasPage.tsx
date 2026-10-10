import QRCode from "qrcode";
import { useEffect, useState } from "react";
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
  return (
    <Card title={p.name}>
      <div className="flex flex-col items-start gap-3 sm:flex-row">
        {url && <Qr url={url} />}
        <div className="space-y-2 text-sm">
          <p>{p.description}</p>
          {url && <p>Scan to subscribe in the ntfy app, or open <a className="break-all underline" href={url} target="_blank" rel="noreferrer">{url}</a>.</p>}
          <p className="font-mono text-xs text-slate-500">topic: {p.ntfy_topic}</p>
        </div>
      </div>
    </Card>
  );
}

/** The four demo personas with ntfy topics, plus DEMO_MODE exposure simulation. */
export default function PersonasPage() {
  const viewers = useAsync(() => api.viewers(), []);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function simulate(body: Parameters<typeof api.simulate>[0], label: string) {
    setError(null);
    try {
      await api.simulate(body);
      setStatus(`${label}. Re-open the facility page to see the effect.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

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
      <Card title="Simulate exposure (DEMO_MODE only)">
        <p className="mb-3 text-sm text-slate-600">
          On a calm day, inject a clearly marked simulated warning or closure to show the exposure flow. Nothing is
          sent until a decision is recorded and sent.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => simulate({ kind: "zone_alert", zone: "MOZ043" }, "Simulated Winter Storm Warning for Cass County, MO (MOZ043)")}>
            Warning for Cass County, MO (Eli's home)
          </Button>
          <Button variant="secondary" onClick={() => simulate({ kind: "road_closure", route: "I-49" }, "Simulated I-49 closure")}>
            Close I-49
          </Button>
          <Button variant="secondary" onClick={() => simulate({ kind: "clear" }, "Simulations cleared")}>Clear simulations</Button>
        </div>
        {status && <p className="mt-3 text-sm text-green-800">{status}</p>}
        {error && <div className="mt-3"><ErrorBox error={error} /></div>}
      </Card>
    </div>
  );
}
