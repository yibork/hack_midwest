import { useCallback, useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { NEEDS_PERSON_MESSAGE, type Confidence, type EvidenceStatus, type Posture } from "../api";

// ---------------------------------------------------------------- data loading

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, setData, error, loading, reload };
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-500 py-4">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
      {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  if (error === NEEDS_PERSON_MESSAGE) {
    // Not an error: the demo role switcher needs a person. The switcher shows the same prompt.
    return (
      <div role="status" className="rounded border border-slate-300 bg-slate-50 p-3 text-slate-700">
        Choose who you're viewing as in the menu.
      </div>
    );
  }
  return (
    <div className="rounded border border-red-300 bg-red-50 p-3 text-red-800 flex items-start justify-between gap-4">
      <div>
        <span className="font-semibold">Error: </span>
        {error}
      </div>
      {onRetry && (
        <button onClick={onRetry} className="text-sm underline shrink-0">
          Retry
        </button>
      )}
    </div>
  );
}

export function Card({ title, children, actions, className = "" }: {
  title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-600">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Button({ variant = "primary", className = "", ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const styles = {
    primary: "bg-slate-800 text-white hover:bg-slate-700",
    secondary: "bg-white text-slate-800 border border-slate-300 hover:bg-slate-50",
    danger: "bg-red-700 text-white hover:bg-red-600",
  }[variant];
  return (
    <button
      {...props}
      className={`rounded px-4 py-2 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed ${styles} ${className}`}
    />
  );
}

// ---------------------------------------------------------------- posture / status

export const POSTURE_STYLE: Record<Posture, string> = {
  normal: "bg-green-700 text-white",
  delayed_arrival: "bg-yellow-400 text-slate-900",
  early_dismissal: "bg-orange-500 text-white",
  telework: "bg-blue-700 text-white",
  closure: "bg-red-700 text-white",
};

export const postureLabel = (p: Posture) => p.replace("_", " ").toUpperCase();

export function PostureBadge({ posture, size = "md" }: { posture: Posture; size?: "sm" | "md" | "xl" }) {
  const sz = {
    sm: "px-2 py-0.5 text-xs",
    md: "px-3 py-1 text-sm",
    xl: "px-6 py-4 text-4xl md:text-6xl tracking-tight",
  }[size];
  return <span className={`inline-block rounded font-extrabold ${sz} ${POSTURE_STYLE[posture]}`}>{postureLabel(posture)}</span>;
}

export function StatusBadge({ status }: { status: EvidenceStatus }) {
  const s = {
    ok: "bg-green-100 text-green-800",
    stale: "bg-amber-200 text-amber-900 ring-2 ring-amber-500",
    unknown: "bg-red-600 text-white ring-2 ring-red-800",
  }[status];
  return <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${s}`}>{status}</span>;
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const s = { high: "bg-green-100 text-green-800", medium: "bg-amber-100 text-amber-900", low: "bg-red-100 text-red-800" }[confidence];
  return <span className={`rounded px-2 py-0.5 text-sm font-bold uppercase ${s}`}>{confidence} confidence</span>;
}

// ---------------------------------------------------------------- formatting

export function fmtTime(iso: string | null | undefined, tz?: string, withDate = false): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    timeZone: tz,
    ...(withDate ? { month: "short", day: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: tz ? "short" : undefined,
  }).format(new Date(iso));
}

export function fmtDateTime(iso: string | null | undefined, tz?: string): string {
  return fmtTime(iso, tz, true);
}
