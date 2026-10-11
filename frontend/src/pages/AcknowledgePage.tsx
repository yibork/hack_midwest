import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, type Notification } from '../api';
import { Button, Card, ErrorBox, fmtDateTime, humanText } from '../components/ui';

/** Frontend-only acknowledgment view. The legacy server-rendered /ack link is unchanged. */
export default function AcknowledgePage() {
  const {id} = useParams();
  const [notice, setNotice] = useState<Notification | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function acknowledge() {
    if (!id) return;
    setBusy(true); setError(null);
    try {setNotice(await api.ack(id));}
    catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  return <main className="p-4"><div className="personal-status"><Card>
    <p className="mb-4 text-sm font-semibold">Closure Call · DEMO · Synthetic employee data</p>
    <h1 className="text-2xl font-bold">{notice ? 'Thank you. Your acknowledgment is recorded.' : 'Confirm you received your facility notice.'}</h1>
    <p className="instruction mt-4">{notice ? humanText(notice.duty_instruction) : 'Please acknowledge the message you were sent.'}</p>
    {notice && <p className="mt-3 text-sm">Recorded {fmtDateTime(notice.acknowledged_at)}.</p>}
    {notice?.message?.match(/SIMULATED|REPLAY/) && <p className="mt-2 font-semibold">{notice.message.includes('SIMULATED') ? 'SIMULATED' : 'REPLAY'} · Not a live operating instruction</p>}
    {notice ? <a className="mt-5 inline-block rounded bg-blue-700 px-6 py-3 font-semibold text-white" href={`/me?as=${notice.employee_id}`}>View my status</a>
      : <Button className="mt-5 w-full !py-3 !text-lg" disabled={busy || !id} onClick={acknowledge}>{busy ? 'Recording…' : 'I got this: acknowledge'}</Button>}
    {error && <div className="mt-3"><ErrorBox error={error} /></div>}
  </Card></div></main>;
}
