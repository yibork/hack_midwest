import {useEffect} from 'react';
import {useParams, useNavigate} from 'react-router-dom';
import {api, type Evidence} from '../api';
import {Card, ErrorBox, Loading, useAsync} from '../components/ui';

export default function SavedEvidencePage() {
  const {id = ''} = useParams();
  const navigate = useNavigate();
  const evidence = useAsync(() => api.evidence(id), [id]);
  const hash = evidence.data?.saved_copy_hash || evidence.data?.snapshot_hash;
  useEffect(() => {if (hash) navigate(`/saved-copy/${hash}${window.location.search}`, {replace: true});}, [hash, navigate]);
  return <main className="p-4"><Card title="Read-only · Saved evidence">
    {evidence.loading && <Loading />}{evidence.error && <ErrorBox error={evidence.error} />}
    {evidence.data && !hash && <p>No original saved copy exists for this historical evidence. <details><summary>Technical details</summary><pre>{JSON.stringify(evidence.data as Evidence, null, 2)}</pre></details></p>}
  </Card></main>;
}
