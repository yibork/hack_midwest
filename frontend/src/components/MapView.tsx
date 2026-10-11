import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type MapData, type MapFact } from "../api";
import { Card, ErrorBox, Loading, useAsync } from "./ui";

const RELEVANCE: Record<string, { label: string; color: string }> = {
  facility: { label: "Access route", color: "#b91c1c" },
  commute: { label: "Commute", color: "#d97706" },
  not_relevant: { label: "Not relevant", color: "#64748b" },
  uncertain: { label: "Uncertain location", color: "#7c3aed" },
};
const PROVENANCE: Record<string, string> = {
  source_coordinates: "source coordinates",
  geocoded_text: "geocoded from text (approximate)",
  none: "no location",
};

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function popup(f: MapFact): string {
  const since = f.active_since ? new Date(f.active_since).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : null;
  const link = /^https?:\/\//.test(f.link ?? "") ? `<a href="${esc(f.link)}" target="_blank" rel="noreferrer">source</a>` : "";
  const saved = f.snapshot_url ? ` · <a href="${esc(f.snapshot_url)}" target="_blank" rel="noreferrer">saved copy</a>` : "";
  return `<div style="max-width:260px">
    <strong>${esc(f.title)}</strong><br/>
    <em>“${esc(f.quote)}”</em><br/>
    ${esc(RELEVANCE[f.relevance ?? ""]?.label ?? f.relevance)}${f.affected_commutes ? ` (affects ${f.affected_commutes} commutes)` : ""}
    · ${f.distance_km != null ? `${esc(f.distance_km)} km from facility` : "distance unknown"}<br/>
    ${f.category && f.category !== "other" ? `${esc(f.category)}, ` : ""}${since ? `active since ${esc(since)}` : "undated"}<br/>
    <small>${esc(f.source_name)} · location: ${esc(PROVENANCE[f.location_provenance] ?? f.location_provenance)}<br/>${esc(f.location_note)}</small><br/>
    ${link}${saved}
  </div>`;
}

/** Facility, access segments, aggregated commute corridors (no homes), and located facts. */
export function FactsMap({ data, height = 420 }: { data: MapData; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const sources = useMemo(() => [...new Set(data.facts.map((f) => f.source_name ?? "?"))], [data]);
  const [rel, setRel] = useState<Record<string, boolean>>({ facility: true, commute: true, not_relevant: true, uncertain: true });
  const [src, setSrc] = useState<Record<string, boolean>>({});
  const showSrc = (s: string) => src[s] !== false;

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { scrollWheelZoom: false }).setView([data.facility.lat, data.facility.lon], 11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, [data.facility.lat, data.facility.lon]);

  useEffect(() => {
    const g = layer.current;
    if (!g) return;
    g.clearLayers();
    for (const c of data.corridor_cells) {
      L.rectangle(c.bounds as L.LatLngBoundsExpression, {
        stroke: false, fillColor: "#f59e0b", fillOpacity: Math.min(0.08 + c.count * 0.015, 0.3), interactive: false,
      }).addTo(g);
    }
    for (const s of data.segments) {
      L.polyline(s.coordinates.map(([lon, lat]) => [lat, lon]) as L.LatLngExpression[], { color: "#0f172a", weight: 6, opacity: 0.7 })
        .bindTooltip(`${s.name} (access segment, ${s.buffer_km} km buffer)`).addTo(g);
    }
    L.circleMarker([data.facility.lat, data.facility.lon], { radius: 9, color: "#0f172a", weight: 3, fillColor: "#facc15", fillOpacity: 1 })
      .bindTooltip(data.facility.name).addTo(g);
    for (const f of data.facts) {
      if (!rel[f.relevance ?? "uncertain"] || !showSrc(f.source_name ?? "?") || !f.geometry) continue;
      const color = RELEVANCE[f.relevance ?? "uncertain"]?.color ?? "#64748b";
      const geocoded = f.location_provenance === "geocoded_text";
      L.geoJSON(f.geometry as GeoJSON.GeoJsonObject, {
        style: { color, weight: 5, opacity: 0.85, dashArray: geocoded ? "6 6" : undefined },
        pointToLayer: (_feat, latlng) => L.circleMarker(latlng, {
          radius: 7, color, weight: geocoded ? 2 : 3, dashArray: geocoded ? "3 3" : undefined,
          fillColor: geocoded ? "#ffffff" : color, fillOpacity: geocoded ? 0.6 : 0.85,
        }),
      }).bindPopup(popup(f)).addTo(g);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, rel, src]);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {Object.entries(RELEVANCE).map(([k, v]) => (
          <label key={k} className="flex items-center gap-1">
            <input type="checkbox" checked={rel[k]} onChange={(e) => setRel({ ...rel, [k]: e.target.checked })} />
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: v.color }} />{v.label}
          </label>
        ))}
        <span className="text-slate-400">|</span>
        {sources.map((s) => (
          <label key={s} className="flex items-center gap-1">
            <input type="checkbox" checked={showSrc(s)} onChange={(e) => setSrc({ ...src, [s]: e.target.checked })} />
            {s.split(" (")[0]}
          </label>
        ))}
      </div>
      <div ref={el} style={{ height }} className="z-0 rounded border border-slate-200" role="region" aria-label="Map of local facts" />
      <p className="mt-1 text-xs text-slate-500">
        Filled markers: source coordinates. Dashed white markers: geocoded from text (approximate; never used for the
        access-route rule). Dark lines: access segments. Orange bands: {data.corridor_rule}
        {data.unmapped.length > 0 && ` ${data.unmapped.length} fact(s) have no location and are listed, not mapped.`}
      </p>
    </div>
  );
}

/** Loads the map for a recommendation. */
export default function MapView({ recId, title = "Map" }: { recId: string; title?: string }) {
  const data = useAsync(() => api.map(recId), [recId]);
  return (
    <Card title={title}>
      {data.loading && <Loading />}
      {data.error && <ErrorBox error={data.error} onRetry={data.reload} />}
      {data.data && <FactsMap data={data.data} />}
    </Card>
  );
}
