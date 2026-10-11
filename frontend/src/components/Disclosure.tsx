import {useState, type ReactNode} from 'react';

export default function Disclosure({title, summary, children}: {title: string; summary: string; children: ReactNode}) {
  const key = `closurecall:section:${title}`;
  const [open, setOpen] = useState(() => {try {return localStorage.getItem(key) === 'open';} catch {return false;}});
  return <details className="rounded border border-slate-300 bg-white p-3" open={open} onToggle={event => {
    const value = event.currentTarget.open; setOpen(value);
    try {localStorage.setItem(key, value ? 'open' : 'closed');} catch {/* Private browsing is supported. */}
  }}><summary className="cursor-pointer"><strong>{title}</strong><span className="ml-3 text-sm text-slate-600">{summary}</span></summary>
    {open && <div className="mt-3">{children}</div>}
  </details>;
}
