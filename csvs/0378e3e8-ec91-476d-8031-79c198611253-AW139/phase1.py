import argparse
import pandas as pd
import json
from pathlib import Path
import orjson
import sqlite3
from collections import Counter
from tools import init_db

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=True)
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    csv_path = Path(args.csv).resolve()
    db_path = workdir / 'graph.db'
    profile_path = workdir / 'asset_profile.json'

    # Fix schema bug by initializing without content/content_rowid for fts
    with sqlite3.connect(db_path) as conn:
        with open('D:/work/openclaude/sparengine-export/phases/schema.sql', 'r') as f:
            schema = f.read()
        schema = schema.replace("content='pages',", "")
        schema = schema.replace("content_rowid='rowid'", "")
        conn.executescript(schema)
        conn.commit()

    with open(profile_path, 'r') as f:
        profile = json.load(f)

    blocked_sn_list = profile.get('blocked_sn_list', [])
    blocked_set = set([str(b).strip().upper() for b in blocked_sn_list if b])

    df_iter = pd.read_csv(csv_path, chunksize=500)
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')

    rows_processed = 0
    docs_inserted = 0
    pages_inserted = 0
    stamps_inserted = 0

    doc_weights = {}

    for chunk in df_iter:
        conn.execute('BEGIN')
        try:
            for idx, row in chunk.iterrows():
                rows_processed += 1
                try:
                    ext = orjson.loads(row['extracted_json'])
                except:
                    continue

                doc_id = str(row.get('document_id', ''))
                page_id = str(row.get('id', ''))
                file_name = str(row.get('file_name', ''))
                page_index = int(row.get('page_index', 0))
                original_path = str(row.get('original_path', ''))
                enhanced_s3_key = str(row.get('enhanced_s3_key', ''))

                is_blank = ext.get('is_blank', False)
                is_template_empty = ext.get('is_template_empty', False)
                rotation_hint = ext.get('rotation_hint', 0)

                doc_type = ext.get('document_type', '')
                evidentiary_weight = ext.get('evidentiary_weight', 'reference')
                title = ext.get('title', '')

                meta = ext.get('metadata', {})
                is_mis_export = meta.get('is_mis_export', False)
                mis_system = meta.get('mis_system', '')
                context_discrepancy = meta.get('context_discrepancy', '')

                ents = ext.get('entities', [])
                stamps = ext.get('stamps_and_signatures', [])
                sections = ext.get('sections', [])

                if doc_id not in doc_weights:
                    doc_weights[doc_id] = []
                    conn.execute("INSERT OR IGNORE INTO documents (id, file_name, document_type, evidentiary_weight, is_mis_export, mis_system, title, original_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (doc_id, file_name, doc_type, evidentiary_weight, int(is_mis_export), mis_system, title, original_path))
                    docs_inserted += 1

                doc_weights[doc_id].append(evidentiary_weight)

                text_content = title + '\n' if title else ''
                for sec in sections:
                    if isinstance(sec, dict) and 'data' in sec:
                        text_content += str(sec['data']) + '\n'
                        if 'entities' in sec:
                            ents.extend(sec['entities'])

                if is_blank:
                    text_content = ''

                ata_chapters = meta.get('ata_chapters', [])
                part_numbers = meta.get('part_numbers', [])
                serial_numbers = meta.get('serial_numbers', [])
                reference_numbers = meta.get('reference_numbers', [])
                regulatory_references = meta.get('regulatory_references', [])

                if not serial_numbers and not part_numbers:
                    for e in ents:
                        if 'value' in e:
                            v = str(e['value']).strip()
                            etype = e.get('entity_type')
                            if etype == 'serial_number': serial_numbers.append(v)
                            elif etype == 'part_number': part_numbers.append(v)
                            elif etype == 'ata_chapter': ata_chapters.append(v)

                filtered_sns = []
                for sn in serial_numbers:
                    if str(sn).upper() not in blocked_set:
                        filtered_sns.append(sn)

                date_val = meta.get('dates', [None])[0] if meta.get('dates') else None

                conn.execute("INSERT OR IGNORE INTO pages (id, document_id, page_index, document_type, evidentiary_weight, is_blank, is_template_empty, rotation_hint, is_mis_export, mis_system, title, date, enhanced_s3_key, text_content, ata_chapters, part_numbers, serial_numbers, reference_numbers, regulatory_references, context_discrepancy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (page_id, doc_id, page_index, doc_type, evidentiary_weight, int(is_blank), int(is_template_empty), rotation_hint, int(is_mis_export), mis_system, title, date_val, enhanced_s3_key, text_content, json.dumps(ata_chapters), json.dumps(part_numbers), json.dumps(filtered_sns), json.dumps(reference_numbers), json.dumps(regulatory_references), context_discrepancy))
                pages_inserted += 1

                cur = conn.execute("SELECT rowid FROM pages WHERE id = ?", (page_id,))
                row = cur.fetchone()
                if row:
                    rowid = row[0]
                    conn.execute("INSERT INTO pages_fts (rowid, page_id, text_content, file_name, document_type) VALUES (?, ?, ?, ?, ?)", (rowid, page_id, text_content, file_name, doc_type))

                for st in stamps:
                    st_id = st.get('stamp_id', st.get('id', ''))
                    if not st_id: continue
                    full_id = f"{page_id}::{st_id}"
                    binds = st.get('binds_to', {})
                    conn.execute("INSERT OR IGNORE INTO stamps (id, page_id, stamp_local_id, type, text, person_name, title_role, date, certificate_number, location_context, binds_to_target_kind, binds_to_target_ref, binding_confidence, binding_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (full_id, page_id, st_id, st.get('type'), st.get('text'), st.get('person_name'), st.get('title_role'), st.get('date'), st.get('certificate_number'), st.get('location_context'), binds.get('target_type'), binds.get('target_ref'), binds.get('binding_confidence'), binds.get('binding_reason')))
                    stamps_inserted += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error processing chunk: {e}")

    for doc_id, weights in doc_weights.items():
        if weights:
            mode_weight = Counter(weights).most_common(1)[0][0]
            conn.execute("UPDATE documents SET evidentiary_weight = ? WHERE id = ?", (mode_weight, doc_id))
    conn.commit()

    cur = conn.cursor()
    cur.execute("SELECT 'pages' AS t, COUNT(*) AS n FROM pages UNION ALL SELECT 'documents', COUNT(*) FROM documents UNION ALL SELECT 'stamps', COUNT(*) FROM stamps UNION ALL SELECT 'pages_fts', COUNT(*) FROM pages_fts")
    res = cur.fetchall()
    counts = {r[0]: r[1] for r in res}

    cur.execute("SELECT COUNT(*) FROM pages WHERE text_content != ''")
    non_empty_pages = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM documents WHERE document_type IS NOT NULL AND document_type != ''")
    typed_docs = cur.fetchone()[0]

    with open(workdir / 'progress.log', 'a') as f:
        f.write("\n== Phase 1 verification ==\n")
        f.write(f"- csv_row_count                       : {rows_processed}\n")
        for k, v in counts.items():
            f.write(f"- count({k})".ljust(38) + f": {v}\n")
        f.write(f"- count(pages WHERE text_content != ''): {non_empty_pages}\n")
        f.write(f"- pct documents with non-null document_type : {int(typed_docs / max(1, counts['documents']) * 100)}%\n")

if __name__ == '__main__':
    main()