# PHASE 5 — Event Hydration

**Intent.** Insert every event from `extracted_json.content.events[]` into `events`, plus events derived from sections and tables.

**Reference files:**
- `csv_and_ocr.md` (events shape, sections, tables)
- `finding_types.md` (TASK_NOT_CONFIRMED)

**Inputs:** `pages`, `documents`, `stamps`, `components`, `serials`.

---

## Steps

1. **For each page**, re-parse the `extracted_json` (or stash a parsed copy in Phase 1 if memory allows). Iterate `content.events[]`.

2. **For each event entry, insert into `events`:**
   ```python
   cursor.execute("""
       INSERT INTO events (id, component_id, asset_id, event_type,
           task_compliance_status, compliance_status_reason, event_date,
           work_order_id, mro, tsn_at_event, csn_at_event, description,
           task_reference, file_name, page_index, chunk_id,
           text_evidence, confidence, evidentiary_weight)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   """, (...))
   ```
   - `id` = `f"event::{file_name}::{page_index}::{event_id}"`.
   - `component_id` = resolved from `bound_entities[]` (look up the SN in `serials.serial_number`, return `serials.component_id`).
   - `text_evidence` = the verbatim quote from the page (NOT NULL — if you can't extract a quote, use `description` from the OCR; never use empty string or NULL).
   - `task_compliance_status` and `compliance_status_reason` carried verbatim — **DO NOT re-derive**.

3. **Additional event sources beyond `content.events[]`:**

   - **`defect_entry` sections** → emit two events: an `inspection` (the defect) + a `task_performed` (the corrective action). Both linked to the same WO.
   - **`inspection_finding` sections** → one `inspection` event.
   - **`certification_statement` sections** → one `release_to_service` event. The bound stamp's `person_name` becomes the signing person; `approval_number` becomes a requirement edge in Phase 6.
   - **Parts tables with `S/N Off` and `S/N On`** → one `component_removal` event per Off, one `component_installation` event per On.
   - **LLP tables** → one event per row with `event_type = 'task_performed'` if the row has a "Status: Complied" column, else `event_type = 'inspection'`.
   - **SB/AD compliance tables** → one event per row, mapping the `Status` column to `task_compliance_status`.

4. **For each event with `task_compliance_status` in `{listed_but_not_signed, ambiguous}`**, raise a `TASK_NOT_CONFIRMED` finding (Phase 7 will assess severity per the matrix; for now write it as `provisional`).

5. **For each event with `task_compliance_status == 'marked_not_required'`**: record but do NOT raise a finding (this is valid).

6. **Resolve `bound_stamps[]`** to existing rows in `stamps`. Phase 6 will create the `STAMP_BINDS_TO` edges.

---

## Performance notes

- Re-parsing `extracted_json` is the slowest part. If you parsed in Phase 1 and didn't stash, this re-parse is unavoidable. Use `orjson` and stream.
- One transaction per ~1000 events inserted.

---

## MANDATORY VERIFICATION

```sql
SELECT COUNT(*) AS events_total FROM events;
SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC;
SELECT task_compliance_status, COUNT(*) FROM events GROUP BY task_compliance_status;
SELECT COUNT(*) FROM events WHERE component_id IS NOT NULL;
SELECT COUNT(*) FROM events WHERE text_evidence IS NULL OR text_evidence = '';
```

```
- count(events)                                  : > 0
- count(events) ratio to count(pages)            : ~0.3..3.0 typical
- count(events WHERE component_id IS NOT NULL)   : >= 0.40 * count(events)
                                                    (at least 40% of events linked to components;
                                                     if lower, bound_entities resolution is broken)
- count(events WHERE text_evidence IS NULL OR text_evidence = '') : 0
- distinct event_type count                      : >= 3
- count(events WHERE event_type='task_performed') / count(events) : < 0.95
                                                    (if 95%+ of events are 'task_performed', you
                                                     skipped sections/tables — re-do step 3)
```

**STOP conditions:**

- `count(events) == 0`.
- `count(events WHERE component_id IS NOT NULL) < 0.40 * count(events)`. The agent silently dropped the link from events to components. Inspect: do `serials` rows have `component_id` set in Phase 4? Does `bound_entities[].entity_id` map to an `entities[].entity_id` you stored anywhere? If you didn't preserve entity ids in Phase 1, you can't resolve them now.
- Any event has `text_evidence IS NULL OR text_evidence = ''`. Golden-rule violation; the schema's NOT NULL is disabled.
- `distinct event_type count < 3`. You only built one event source. Re-read step 3 (sections + tables) of this file.
- `count(events WHERE event_type='task_performed') > 0.95 * count(events)`. Same cheat — only one source iterated. Parts tables, defect entries, certification statements should produce other event_types.
