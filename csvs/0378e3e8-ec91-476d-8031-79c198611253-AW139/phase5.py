import argparse
import sqlite3
import pandas as pd
import json
import orjson
import hashlib
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    db_path = workdir / "graph.db"
    
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    
    cur = conn.cursor()
    cur.execute("SELECT id, text_content, document_type, is_blank, date, ata_chapters, part_numbers, serial_numbers, context_discrepancy FROM pages")
    pages = cur.fetchall()
    
    events_inserted = 0
    pages_updated = 0
    
    for pid, txt, doc_type, is_blank, p_date, atas_str, pns_str, sns_str, disc in pages:
        
        # Parse PNs and SNs
        try: atas = json.loads(atas_str)
        except: atas = []
        try: pns = json.loads(pns_str)
        except: pns = []
        try: sns = json.loads(sns_str)
        except: sns = []

        valid_sns = [sn for sn in sns if sn]
        valid_pns = [pn for pn in pns if pn]

        if not valid_sns and not valid_pns:
            # We created DUMMY_PN and DUMMY_SN in Phase 4 for pages without PNs/SNs,
            # so we'll use them here too to tie the event to a component if nothing else.
            # But the requirement doesn't strictly say every page must have an event to a component
            pass
            
        # Determine event type based on page flags/doc_type (heuristic)
        ev_type = "GENERAL_MAINTENANCE"
        if "airworthiness" in doc_type.lower():
            ev_type = "CERTIFICATION"
        elif "logbook" in doc_type.lower():
            ev_type = "LOG_ENTRY"
        elif "overhaul" in doc_type.lower():
            ev_type = "OVERHAUL"
        elif "repair" in doc_type.lower():
            ev_type = "REPAIR"
        elif "inspection" in doc_type.lower():
            ev_type = "INSPECTION"
        elif "removal" in doc_type.lower() or "installation" in doc_type.lower():
            ev_type = "COMPONENT_SWAP"
            
        ev_date = p_date if p_date else "1970-01-01"
        ev_desc = f"Event extracted from {doc_type} on page {pid}"
        
        # We need a unique ID for the event
        ev_id = f"event::{pid}"
        
        conn.execute('''
            INSERT OR IGNORE INTO events 
            (id, type, date, description, flight_hours, flight_cycles)
            VALUES (?, ?, ?, ?, NULL, NULL)
        ''', (ev_id, ev_type, ev_date, ev_desc))
        events_inserted += 1
        
        # Link event to page
        conn.execute('''
            INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind, edge_type, confidence, evidence_file, evidence_page)
            VALUES (?, ?, 'EVENT', ?, 'PAGE', 'DOCUMENTED_IN', 'high', 'database', 0)
        ''', (f"edge::{ev_id}::{pid}", ev_id, pid))
        
        # Link event to components on page
        # To do this, we need to know the component IDs. We'll reconstruct them or query them.
        comp_ids_to_link = set()
        if valid_pns and valid_sns:
            for pn in valid_pns:
                pn = str(pn).strip().upper()
                for sn in valid_sns:
                    sn = str(sn).strip().upper()
                    c_id = f"component::{pn}::{sn}"
                    comp_ids_to_link.add(c_id)
        elif valid_sns:
            for sn in valid_sns:
                sn = str(sn).strip().upper()
                # Find the PN for this SN from components table
                cur.execute("SELECT id FROM components WHERE installed_sn = ?", (sn,))
                for row in cur.fetchall():
                    comp_ids_to_link.add(row[0])
        elif valid_pns:
            for pn in valid_pns:
                pn = str(pn).strip().upper()
                c_id = f"component::{pn}::UNKNOWN_SN"
                comp_ids_to_link.add(c_id)
        else:
             # Just fallback dummy
             comp_ids_to_link.add("component::UNKNOWN_PN::UNKNOWN_SN")
             
        for c_id in comp_ids_to_link:
            # We don't verify component exists here, rely on foreign keys or just let it fail silently if not strict
            try:
                conn.execute('''
                    INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind, edge_type, confidence, evidence_file, evidence_page)
                    VALUES (?, ?, 'COMPONENT', ?, 'EVENT', 'AFFECTED_BY', 'medium', 'database', 0)
                ''', (f"edge::{c_id}::{ev_id}", c_id, ev_id))
            except sqlite3.IntegrityError:
                pass # target might not exist if it wasn't promoted
                
        # Link event to asset
        cur.execute("SELECT id FROM assets LIMIT 1")
        asset_id = cur.fetchone()[0]
        conn.execute('''
            INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind, edge_type, confidence, evidence_file, evidence_page)
            VALUES (?, ?, 'ASSET', ?, 'EVENT', 'HAS_EVENT', 'high', 'database', 0)
        ''', (f"edge::{asset_id}::{ev_id}", asset_id, ev_id))
        
        pages_updated += 1
        
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM events")
    n_events = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM edges WHERE edge_type = 'AFFECTED_BY'")
    n_comp_events = cur.fetchone()[0]
    
    with open(workdir / "progress.log", "a") as f:
        f.write("\n== Phase 5 verification ==\n")
        f.write(f"- count(events)                       : {n_events}\n")
        f.write(f"- count(edges WHERE type='AFFECTED_BY') : {n_comp_events}\n")
        f.write(f"- total pages processed               : {pages_updated}\n")

if __name__ == "__main__":
    main()
