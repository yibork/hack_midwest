import { type Evidence } from "../api";
import {loadViewer} from './RoleContext';

export default function SavedCopyLinks({evidence}: {evidence: Evidence}) {
  const hash = evidence.saved_copy_hash || evidence.snapshot_hash;
  const roleQuery = loadViewer().role === 'auditor' ? '?role=auditor' : '';
  const source = evidence.url && /^https?:\/\//i.test(evidence.url) ? evidence.url : null;
  return <div className="space-y-1">
    {source ? <a className="underline block" href={source} target="_blank" rel="noreferrer">Source (live — may have changed)</a> : <span className="block text-slate-500">No live source</span>}
    {hash ? <><a className="underline block" href={`/api/snapshots/${hash}${roleQuery}`} target="_blank" rel="noreferrer">Saved copy</a>
      {evidence.saved_copy_kind === 'normalized' && <span className="block text-slate-500">Normalized evidence; no original raw response saved</span>}
      <details><summary>Technical details</summary><code className="break-all">{hash}</code></details></>
      : <span className="block text-slate-500">No saved response</span>}
  </div>;
}
