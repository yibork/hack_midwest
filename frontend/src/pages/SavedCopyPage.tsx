import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, type SnapshotVerification } from "../api";
import { Button, Card, ErrorBox, Loading, fmtDateTime, humanText, useAsync } from "../components/ui";

export default function SavedCopyPage() {
  const {digest = ''} = useParams();
  const copy = useAsync(async () => {
    const [raw, info] = await Promise.all([api.snapshotText(digest), api.snapshotVerify(digest)]);
    let text = raw;
    try { text = JSON.stringify(JSON.parse(raw), null, 2); } catch { /* HTML/XML stays escaped text. */ }
    return {text, info};
  }, [digest]);
  const [verification, setVerification] = useState<SnapshotVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function verify() {
    setBusy(true); setError(null);
    try {
      const result = await api.snapshotVerify(digest);
      setVerification(result);
      copy.reload(); // a changed file cannot leave an old, apparently valid display
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-6xl p-4 md:p-6">
    <p className="mb-3 text-sm text-slate-600">Closure Call · DEMO / synthetic facility · Read-only saved copy</p>
    <Card title="Saved evidence copy">
      {copy.loading && <Loading />}
      {copy.error && <ErrorBox error={copy.error} />}
      {copy.data && <>
        {copy.data.info.copies.map((item, index) => <div key={index} className="mb-3 text-sm">
          <strong>{item.scenario_kind === 'simulated' ? 'SIMULATED · ' : item.scenario_kind === 'replay' ? 'REPLAY · ' : ''}{humanText(item.source)} — {humanText(item.kind)}</strong>
          <div>Source URL: {item.source_url && /^https?:\/\//i.test(item.source_url) ? <a className="underline break-all" href={item.source_url} target="_blank" rel="noreferrer">{item.source_url}</a> : 'No live source'}</div>
          <div>Retrieved: {fmtDateTime(item.retrieved_at)} · Source time: {fmtDateTime(item.source_time)}</div>
          {item.copy_kind === 'normalized' && <p className="font-semibold text-amber-800">Saved normalized evidence. The original raw response was not stored.</p>}
        </div>)}
        {!copy.data.info.copies.length && <p className="text-sm">Source URL and retrieval time were not associated with this historical snapshot.</p>}
        <p className="mb-3 text-sm break-all">SHA-256: <code>{digest}</code></p>
        <Button disabled={busy} onClick={verify}>{busy ? 'Verifying…' : 'Verify saved bytes'}</Button>
        <p role="status" className={(verification ?? copy.data.info).match ? 'mt-2 text-green-800' : 'mt-2 font-bold text-red-800'}>
          {(verification ?? copy.data.info).match ? '✓ Hash matches — saved bytes unchanged' : 'Hash mismatch — saved bytes changed'}
        </p>
        {verification && !verification.match && <p className="text-sm break-all">Computed SHA-256: <code>{verification.actual_sha256}</code></p>}
        <p className="my-3 text-sm text-slate-500">Source content below is displayed as text, never executed. JSON is formatted for readability; verification hashes the original stored bytes, not this formatting.</p>
        <pre className="overflow-x-auto border border-slate-200 bg-slate-50 p-3 text-xs">{copy.data.text}</pre>
      </>}
      {error && <ErrorBox error={error} />}
    </Card>
  </main>;
}
