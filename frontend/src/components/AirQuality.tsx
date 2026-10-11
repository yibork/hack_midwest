import type {Evidence} from '../api';
import SavedCopyLinks from './SavedCopyLinks';
import {fmtDateTime} from './ui';

type Pollutant = {pollutant: string; aqi: number; category: string; reporting_area: string;
  observation_time?: string; forecast_date?: string; status: string};

export default function AirQuality({evidence, tz, targetDay}: {evidence: Evidence[]; tz: string; targetDay: string}) {
  const rows = evidence.filter(e => e.source === 'airnow');
  return <div className="mb-3 text-sm" aria-label="Air quality">
    <p className="font-semibold">Air quality — EPA AirNow · target day {targetDay}</p>
    {!rows.length && <p>Unknown — no AirNow evidence collected; not treated as good.</p>}
    {rows.map(e => {
      const value = e.value as {pollutants?: Pollutant[]; error?: string};
      const expired = !!e.valid_until && new Date(e.valid_until).getTime() < Date.now();
      return <div key={e.id} className="mt-1">
        <span className="font-semibold">{e.kind === 'air_quality_current' ? 'Current' : 'Daily forecast'}: </span>
        {value.pollutants?.length ? value.pollutants.map((p, i) => <span key={i}>
          {i > 0 ? ' · ' : ''}{p.pollutant} AQI {p.aqi} — {p.category}, {p.reporting_area}
          {p.observation_time ? ` (${fmtDateTime(p.observation_time, tz)})` : ` (${p.forecast_date})`}
          {(p.status !== 'ok' || e.status !== 'ok' || expired) && ' — stale / unknown; not used as current'}
        </span>) : <span>Unknown — {value.error ?? 'no data'}; not treated as good.</span>}
        <span className="ml-2"><SavedCopyLinks evidence={e} /></span>
      </div>;
    })}
    <p className="mt-1 text-slate-600">Preliminary supplementary data · 25-mile lookup · missing pollutants are unknown. Posture mappings are facility policy choices.</p>
  </div>;
}
