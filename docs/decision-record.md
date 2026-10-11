# Saved copies and decision memos

Evidence tables and Record replay offer **Source (live — may have changed)** and **Saved copy**
links. Opening `/api/snapshots/{sha256}` in a browser redirects to an app-owned, read-only wrapper
with source URL, retrieval time, SHA-256 and a Verify button. API clients receive the original
stored bytes as `text/plain`, with sandbox/nosniff headers. JSON is pretty-printed in an escaped
`<pre>`; HTML/XML is displayed as inert text. Verification hashes the original bytes, not the
display formatting. `/api/snapshots/{sha256}/verify` reports match/mismatch and the computed hash.

Some historical/internal items never had an original raw response saved. They receive a saved
**normalized evidence** envelope, explicitly labeled as such, not a fabricated raw response.
Original values, retrieval times, snapshot references and record payloads are preserved. The
additive provenance backfill appends `saved_copies.backfilled` events. Missing live URLs remain
“No live source”; missing raw files are reported unavailable. SIMULATED/REPLAY labels persist.

## Hash chain

Each event has nullable additive `prev_hash` and `hash` columns. Chains are separate for each
facility; events without a facility use the system chain. Order is append ID, not source time:
replay evidence can have an earlier capture time than the event immediately preceding it.
The genesis predecessor is 64 zeroes. The digest is SHA-256 of predecessor hex text followed by
canonical UTF-8 JSON of `id`, `timestamp`, `facility_id`, `evaluation_id`, `event_type`, `payload`.
Canonical JSON has sorted keys, compact separators and unescaped Unicode; timestamps use UTC
ISO 8601 with microseconds. Record writes still redact secrets before sealing.

An ORM insert listener seals every event, including direct inserts through the mapped model.
`record_chain_heads` serializes writers before allocating IDs (SQLite write lock / PostgreSQL
row lock), avoiding forks across threads/processes. ORM updates/deletes remain refused.
`GET /api/record/verify?facility_id=…` recomputes the chain, checking predecessor links, content
and the stored tail checkpoint. It returns the first broken event, or the current verified head.
Verification reads events and the checkpoint together to avoid races with new appends.

Startup creates new tables and adds **nullable columns only**, then backfills unsealed old events
without changing their content. Restart never repairs an already-sealed event. Historic events
are attested starting at migration time: this does not prove their pre-migration integrity.
A database administrator who rewrites all hashes and checkpoints can defeat an unanchored chain.
Keep a printed/PDF memo outside the database as an external anchor. This is tamper-evident,
not tamper-proof or independently timestamped. Demo reset preserves the entire chain.

## Memo

`GET /decisions/{id}/memo` (also `/api/decisions/{id}/memo`) is manager-only, read-only and
printable via **Print / save PDF**. Links appear after approval on Decide and in Record replay
and decision events. No agents, fetchers or notification sends run when a memo is opened.

It includes target day/calendar explanation, manager decision/override, recommended/final
posture, saved threshold observations/margins/citations and policy version, confidence and
evaluation/decision lead times, alternatives, evidence provenance/hashes/copies, investigation
answers/source labels, delivery/acknowledgment/wave/channel/reason-code/escalation/call-in counts,
and record verification. Original trigger citations are used; missing historical citations are
not silently replaced by today's policy. Private accommodation fields, home coordinates,
individual messages and duty instructions are excluded; free text mentioning private needs is
omitted. Source text is escaped and unsafe URL schemes are not linked.

New decisions save their event ID/hash before notice preparation. Historical decisions resolve
their original `decision.recorded` event. The decision-time anchor does not change when later
notifications arrive. Investigation answers are read from audited events before the decision,
not from a thread's mutable latest result. Delivery stats explicitly state their current reporting
time so later acknowledgments are not represented as known when the decision was approved.

Manual: open the recorded decision, follow **Decision memo**, and use the browser's print-to-PDF
dialog. No new service or environment configuration is needed for this feature.
