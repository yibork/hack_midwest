import { api } from "../api";
import { useAsync } from "./ui";

export default function RecordVerification({facilityId, revision}: {facilityId: string; revision?: unknown}) {
  const check = useAsync(() => api.recordVerify(facilityId), [facilityId, revision]);
  return <div className="rounded border border-slate-300 bg-white p-3 text-sm" aria-live="polite">
    {check.loading ? 'Verifying decision record…' : check.error ? 'Record verification unavailable' : check.data?.ok
      ? <span className="text-green-800">✓ Record verified · {check.data.event_count} events</span>
      : <span className="text-red-800">Record broken at event {check.data?.first_broken_event_id}: {check.data?.reason}</span>}
    <button className="ml-3 underline" onClick={check.reload}>Verify again</button>
    {check.data && <details className="mt-1"><summary>Technical details</summary><code className="break-all">{check.data.head_hash}</code></details>}
  </div>;
}
