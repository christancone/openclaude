# PHASE 1 — Corpus Indexing

**Intent.** Stream every CSV row, parse `extracted_json` once per row, populate `pages` / `documents` / `stamps`, build the FTS index. **No graph edges yet.**

**Reference files to load alongside:**
- `csv_and_ocr.md`
- `document_types.md`
- `schema.sql` (if `tools.py` doesn't exist yet, copy the CREATE TABLEs from here)

**Inputs:**
- `asset_profile.json` (Phase 0 output) — read `blocked_sn_list`.
- The CSV.

---

## Steps

1. **Initialise the database.** If `tools.py` doesn't have `init_db()`, write it now using the schema in `schema.sql`. Call it before any inserts. Set `PRAGMA foreign_keys = ON`.

2. **Stream the CSV row by row** (use `pandas.read_csv(..., chunksize=500)` or iterate row-wise; do not load 50k+ rows into memory).

3. **For each row, parse `extracted_json` once** (use `orjson.loads` for speed, fallback to `json.loads`). Skip if `pd.isna(row['extracted_json'])` or parse fails (log the failure with the row id).

4. **Extract from the parsed JSON:**
   - `is_blank`, `is_template_empty`, `rotation_hint` — top-level.
   - `content.document_type`, `content.evidentiary_weight`, `content.title`.
   - `content.metadata.is_mis_export`, `mis_system`, `dates`, `reference_numbers`, `ata_chapters`, `regulatory_references`, `context_discrepancy`, `serial_number`, `part_numbers`.
   - `content.entities[]`, `content.events[]`, `content.stamps_and_signatures[]`, `content.sections[]`, `content.tables[]`.

5. **Insert into `documents`** keyed by `row['document_id']` (one row per document). Use `INSERT OR IGNORE`. Set `evidentiary_weight` to the most common across the document's pages (Phase 1 second pass, or update as you go).

6. **Insert into `pages`** — one row per CSV row:
   - `id` = `row['id']`.
   - `text_content` = concatenation of `content.title` + each section's stringified content (used for FTS).
   - `ata_chapters`, `part_numbers`, `serial_numbers`, `reference_numbers`, `regulatory_references` — store as JSON strings.
   - **Apply `blocked_sn_list`** from `asset_profile.json`: drop any SN matching the blocklist before storing in `pages.serial_numbers`.
   - `text_content` may be empty for blank pages — store `''`, not `NULL`.

7. **Insert into `pages_fts`** with the same `text_content`. `pages_fts` is FTS5 with `content='pages'`; you can either INSERT into the virtual table directly or rely on triggers if you defined them in the schema.

8. **Insert into `stamps`** — one row per `stamps_and_signatures[]` entry:
   - `id` = `f"{page_id}::{stamp_local_id}"`.
   - Store `binds_to.target_type`, `target_ref`, `binding_confidence`, `binding_reason` verbatim.

9. **Document-level evidentiary weight** — after all rows: `UPDATE documents SET evidentiary_weight = (SELECT mode of pages.evidentiary_weight ...)` per document.

---

## Performance notes

- `INSERT OR IGNORE` is fine; the unique key is the page UUID.
- Wrap inserts in transactions: one `BEGIN`/`COMMIT` per ~500 rows.
- `tqdm` over the row count gives a useful progress bar.
- Use prepared statements (parameterised queries). String-formatted SQL on 50k rows is slow and unsafe.

---

## What to log

```
- rows processed
- rows skipped (parse fail)
- documents inserted
- pages inserted
- stamps inserted
- entities seen (across pages, before blocklist)
- entities dropped by blocklist
```

---

## MANDATORY VERIFICATION

After Phase 1 finishes, run this SQL and append to `progress.log`:

```sql
SELECT 'pages'      AS t, COUNT(*) AS n FROM pages
UNION ALL SELECT 'documents', COUNT(*) FROM documents
UNION ALL SELECT 'stamps',    COUNT(*) FROM stamps
UNION ALL SELECT 'pages_fts', COUNT(*) FROM pages_fts;
```

Also log:

```
- csv_row_count                       : <N>
- count(pages)                        : must equal csv_row_count (modulo parse failures)
- count(pages WHERE text_content != ''): > 0
- pct documents with non-null document_type : > 50%
```

**STOP conditions** — do NOT proceed to Phase 2 if:

- `count(pages) == 0`.
- `count(documents) == 0`.
- `count(pages)` is wildly off from CSV row count (more than 10% mismatch unexplained by parse failures).
- `count(stamps) == 0` AND the dossier has any pages with `document_type` in the certificate family — that means stamp hydration was silently skipped.

The next phase will read from these tables. If they're empty here, every later phase will be empty too.
