"""Allowlisted, target-day evidence sweep. Refreshes never consume model quota."""
import uuid
import json
from concurrent.futures import ThreadPoolExecutor

from . import investigations as inv, models, record, schemas
from .agents import runtime, weather_agent
from .agents.local import run as local, specialists, roads_cache
from .agents.local.prefilter import prefilter
from .config import settings
from .db import utcnow
from .notify import exposure
from .notify.plan import load_inputs
from .services import store_evidence
from .sources import airnow, forecaster, local_signals as ls, nws, nwps


def request(db, rec, *, launch=True):
    with inv._run_lock:
        accepted = inv.request(db, rec, schemas.InvestigationIn(action="collect_evidence", question="Collect more evidence"), launch=False)
        row = db.get(models.Investigation, accepted["investigation_id"])
        _, _, old = inv.context(db, rec)
        pending = inv.revise(db, row, rec, old, pending=True)
        record.append(db, "evidence_sweep_requested", {"investigation_id": row.id, "target_day": rec.operating_date,
                      "recommendation_id": rec.id}, facility_id=rec.facility_id, evaluation_id=row.id)
        db.commit()
        accepted["recommendation_id"] = pending.id
    if launch:
        inv.start(row.id)
    return accepted


def item_content(item):
    return json.dumps({k: item.get(k) for k in ("item_id", "title", "excerpt", "points", "geometry", "effective_from", "effective_until", "category", "active_since")}, sort_keys=True)


def run(db, row, rec):
    fac, windows, old = inv.context(db, rec)
    pending = db.get(models.Recommendation, row.revised_recommendation_id)
    state = runtime.new_state(evaluation_id=row.id, facility_id=fac.id, operating_date=rec.operating_date,
                              investigation_budget_key=row.budget_key)
    checks, fresh, notes = [], [], []
    row.status = "running"
    db.commit()

    def save(items):
        store_evidence(db, items, fac.id, row.id)
        db.commit()
        fresh.extend(items)

    def step(title, callback):
        inv.event(state, "agent.tool_call_start", tool=title)
        try:
            callback()
            status = "current"
        except Exception as exc:
            status = "failed"
            notes.append(f"{title}: unavailable ({type(exc).__name__})")
            # Unknown replaces old weather evidence, rather than silently reusing it.
            kinds = {"NWS facility alerts": ["active_alerts"], "NWS hourly and gridpoint forecast": ["hourly_forecast", "wind_forecast"],
                     "NWS employee home zones": ["home_zone_alerts"]}.get(title, [])
            save([schemas.Evidence(id=uuid.uuid4().hex, source="nws", kind=k, value={"error": "Refresh failed"},
                                  status="unknown", retrieved_at=utcnow()) for k in kinds])
        checks.append({"source": title, "status": status})
        row.result = {"source_checks": checks.copy(), "limitations": notes.copy()}
        db.commit()
        inv.event(state, "agent.tool_call_end", tool=title, status=status)

    try:
        if rec.scenario_kind in {"simulated", "replay"}:
            # Never combine live source responses with recorded or injected evidence.
            notes.append("Saved scenario only; live refresh and model calls skipped to prevent mixing evidence")
            revised = inv.finish_revision(db, row, rec, pending, old)
        else:
            nws.clear_cache()
            ls.clear_cache()
            people = load_inputs(db, db.get(models.Facility, fac.id), rec.operating_date)[0] if not fac.temporary else []
            step("NWS facility alerts", lambda: weather_agent.fetch_alerts(fac, row.id))
            step("NWS hourly and gridpoint forecast", lambda: weather_agent.fetch_forecast(fac, row.id, operating_date=rec.operating_date))
            step("NWS employee home zones", lambda: exposure.collect(fac.id, row.id, people, save_raw=True))
            step("EPA AirNow air quality", lambda: save(airnow.collect(fac, rec.operating_date)))
            from .demo_location import unknown, local_feeds
            step("NOAA KCDM7 river gauge", lambda: save([unknown("river_categories", "nwps")] if fac.demo_origin_id else nwps.collect(windows)))
            fresh += weather_agent.evidence_for(row.id)
            products = []
            def fetch_products():
                products.extend(forecaster.collect(rec.operating_date, fac.nws_office) if fac.demo_origin_id else forecaster.collect(rec.operating_date))
                save(products)
            step("NWS " + (fac.nws_office or "EAX") + " forecaster products", fetch_products)
            sources = ls.sources()
            inv.event(state, "agent.tool_call_start", tool="Local roads, schools, public safety and news")
            if fac.demo_origin_id:
                feeds = local_feeds(fac, sources)
            else:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    feeds = list(pool.map(ls.fetch, sources))
            inv.event(state, "agent.tool_call_end", tool="Local roads, schools, public safety and news", summary=f"{len(feeds)} allowlisted sources checked")
            old_feeds = {e.value.get("source_id"): e for e in old if e.kind == "local_feed" and isinstance(e.value, dict)}
            work, src_of = {}, {}
            geo = local.geo_context(fac, people)
            routes = sorted({r for p in people for r in p.commute_routes})
            for feed in feeds:
                prior = old_feeds.get(feed.source.id)
                changed = prior is None or prior.value.get("content_hash") != feed.content_hash
                status = "failed" if feed.status != "ok" else "new" if prior is None else "changed" if changed else "current"
                checks.append({"source": feed.source.name, "status": status})
                save([feed.to_evidence()])
                record.append(db, "evidence_sweep.source_refreshed", {"source": feed.source.id, "status": status}, facility_id=fac.id, evaluation_id=row.id)
                items = [{**it, "authority": feed.source.authority, "source_name": feed.source.name, "snapshot_hash": feed.snapshot_hash} for it in feed.items]
                for it in items:
                    src_of[it["item_id"]] = feed.source
                relevant = prefilter(items, fac.route_names, routes)
                previous_items = {it["item_id"]: item_content(it) for it in (prior.value.get("items", []) if prior else [])}
                new_items = [it for it in relevant if previous_items.get(it["item_id"]) != item_content(it)]
                unchanged_ids = {it["item_id"] for it in items if previous_items.get(it["item_id"]) == item_content(it)}
                record.append(db, "evidence_sweep.items_changed", {"source": feed.source.id,
                    "new_item_ids": [it["item_id"] for it in items if it["item_id"] not in previous_items],
                    "changed_item_ids": [it["item_id"] for it in items if it["item_id"] in previous_items and it["item_id"] not in unchanged_ids],
                    "removed_item_ids": sorted(set(previous_items) - {it["item_id"] for it in items}) if feed.status == "ok" else []},
                    facility_id=fac.id, evaluation_id=row.id)
                if changed and feed.status == "ok":
                    work.setdefault(feed.source.agent, []).extend(new_items)
                # Re-ground unchanged facts in this refresh; failures remain explicitly stale.
                for e in old:
                    if e.kind == "local_fact" and isinstance(e.value, dict) and e.value.get("source_id") == feed.source.id and (feed.status != "ok" or e.value.get("item_id") in unchanged_ids):
                        copy = schemas.Evidence.model_validate(e).model_copy(update={"id": uuid.uuid4().hex,
                            "retrieved_at": feed.retrieved_at if feed.status == "ok" else e.retrieved_at,
                            "valid_until": feed.to_evidence().valid_until if feed.status == "ok" else e.valid_until,
                            "snapshot_hash": None,
                            "value": {**e.value, "snapshot_hash": feed.snapshot_hash if feed.status == "ok" else e.value.get("snapshot_hash")},
                            "status": "ok" if feed.status == "ok" else "stale", "saved_copy_hash": None, "saved_copy_kind": None})
                        save([copy])
            for agent, items in work.items():
                if not items:
                    continue
                use_model = runtime.llm_configured() and inv.budget(db, rec)["used"] < inv.budget(db, rec)["limit"]
                hashes = {f.source.id: f.content_hash for f in feeds if f.source.agent == "roads"}
                key = roads_cache.cache_key(fac.id, rec.operating_date, roads_cache.geometry_signature(geo), hashes) if agent == "roads" else None
                hit = roads_cache.get(key)
                if hit:
                    facts, how, rejected = [entry["fact"] for entry in hit.entries if entry["fact"]["item_id"] in {it["item_id"] for it in items}], "cache", []
                else:
                    facts, how, rejected = specialists.run_or_fallback(agent, items, {"name": fac.name, "access_routes": fac.route_names}, routes,
                        state, settings.local_agent_timeout, use_model=use_model, geo=geo)
                by_id = {it["item_id"]: it for it in items}
                save([local.fact_evidence(f, by_id[f["item_id"]], src_of[f["item_id"]], agent, how, utcnow()) for f in facts])
                if agent == "roads":
                    all_items = {it["item_id"]: it for feed in feeds if feed.source.agent == "roads" for it in feed.items}
                    entries = [{"fact": e.value, "item": all_items[e.value["item_id"]], "source_id": e.value["source_id"]} for e in fresh if e.kind == "local_fact" and e.value.get("agent") == "roads" and e.value.get("item_id") in all_items]
                    roads_cache.put(key, fac.id, rec.operating_date, hashes, entries, rejected, origin="evidence_sweep", evaluation_id=row.id)
            remaining = inv.budget(db, rec)["used"] < inv.budget(db, rec)["limit"]
            if remaining and runtime.llm_configured():
                summary, note = forecaster.summarize(products, rec.operating_date, state)
                notes.append(note)
                if summary:
                    save([summary])
            else:
                notes.append("AI summaries skipped: shared model budget exhausted or LLM unavailable. All deterministic refreshes and rules still ran.")
            # Fresh statuses reflect actual response validity, not simply HTTP success.
            for title, kinds in {"NWS facility alerts": {"active_alerts"}, "NWS hourly and gridpoint forecast": {"wind_forecast", "hourly_forecast"},
                                 "NWS employee home zones": {"home_zone_alerts"}, "NOAA KCDM7 river gauge": {"river_categories", "river_forecast"},
                                 "EPA AirNow air quality": {"air_quality_current", "air_quality_forecast"},
                                 "NWS EAX forecaster products": {"forecaster_afd", "forecaster_hwo"}}.items():
                if any(e.kind in kinds and e.status != "ok" for e in fresh):
                    next(c for c in checks if c["source"] == title)["status"] = "failed"
            old_context = [e for e in old if e.kind not in {"evidence_sweep_failure", "forecaster_summary"}]
            revised = inv.finish_revision(db, row, rec, pending, inv.merge_evidence(old_context, fresh))
            groups = {"NWS hourly and gridpoint forecast": {"point": "NWS forecast location", "hourly_forecast": "NWS hourly forecast", "wind_forecast": "NWS gridpoint wind forecast"},
                      "NWS employee home zones": {"zone_alerts": "NWS employee home-zone alerts"},
                      "NOAA KCDM7 river gauge": {"river_categories": "NOAA KCDM7 flood categories", "river_forecast": "NOAA KCDM7 river forecast"},
                      "NWS EAX forecaster products": {"forecaster_afd": "NWS EAX Area Forecast Discussion", "forecaster_hwo": "NWS EAX Hazardous Weather Outlook"}}
            for title, kinds in groups.items():
                parts = list({e.id: e for e in fresh if e.kind in kinds}.values())
                if not parts:
                    continue
                checks = [c for c in checks if c["source"] != title]
                for e in parts:
                    prior = next((p for p in old if p.kind == e.kind and p.url == e.url), None)
                    state_name = "failed" if e.status != "ok" else "new" if prior is None else "changed" if e.snapshot_hash != prior.snapshot_hash else "current"
                    checks.append({"source": kinds[e.kind] + (f" ({e.value.get('zone')})" if e.kind == "zone_alerts" else ""), "status": state_name})
        summary = next((e for e in fresh if e.kind == "forecaster_summary"), None)
        product = next((e for e in fresh if summary and e.id == summary.value.get("product_evidence_id")), None)
        row.status = "completed"
        row.result = {"answer": f"For {rec.operating_date}, evidence collection finished. {sum(c['status'] in {'new', 'changed'} for c in checks)} sources are new or changed. Rules re-evaluated the saved target-day windows.",
            "source_label": "Deterministic evidence sweep", "source_checks": checks, "limitations": notes,
            "tools_used": [c["source"] for c in checks], "new_evidence_ids": [e.id for e in fresh], "target_date": str(rec.operating_date),
            "forecaster_summary": {**summary.value, "saved_copy_hash": product.snapshot_hash} if summary and product else None}
        record.append(db, "evidence_sweep.summary", row.result, facility_id=fac.id, evaluation_id=row.id)
        db.commit()
    except Exception as exc:
        db.rollback()
        # A failed optional stage must not strand the manager on a permanent preliminary.
        # Preserve partial refreshes, and explicitly mark the missing sweep as unknown.
        failure = schemas.Evidence(id=uuid.uuid4().hex, source="sweep", kind="evidence_sweep_failure",
            value={"error": f"Sweep incomplete ({type(exc).__name__})"}, status="unknown", retrieved_at=utcnow())
        store_evidence(db, [failure], fac.id, row.id)
        db.commit()
        inv.finish_revision(db, row, rec, pending, inv.merge_evidence(old, weather_agent.evidence_for(row.id)))
        row.status = "failed"
        row.result = {"answer": f"Evidence sweep for {rec.operating_date} could not finish ({type(exc).__name__}). Partial evidence was re-evaluated with an explicit missing-data flag; collect again from this revision.", "source_checks": checks,
                      "source_label": "Deterministic failure summary", "limitations": notes}
        db.commit()
