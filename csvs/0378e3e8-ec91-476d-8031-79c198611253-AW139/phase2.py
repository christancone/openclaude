import argparse
import pandas as pd
import json
from pathlib import Path
import sqlite3
from collections import Counter
import re

def fuzzy_match(a, b):
    if not a and not b: return True
    if not a or not b: return False
    # Very basic normalize
    a_norm = re.sub(r'[^A-Z0-9]', '', str(a).upper())
    b_norm = re.sub(r'[^A-Z0-9]', '', str(b).upper())
    return a_norm == b_norm

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    db_path = workdir / "graph.db"
    profile_path = workdir / "asset_profile.json"
    log_path = workdir / "progress.log"
    
    with open(profile_path, 'r') as f:
        profile = json.load(f)
        
    asset_class = str(profile.get('asset_class', 'AIRCRAFT')).upper()
    subtype = profile.get('subtype')
    type_designation = profile.get('type_designation')
    tcds = profile.get('tcds')
    yom = profile.get('yom')
    ident = profile.get('identifier', {})
    msn = ident.get('msn')
    esn = ident.get('esn')
    primary_serial = ident.get('primary_serial')
    
    reg_obj = profile.get('registration', {})
    reg = reg_obj.get('current')
    reg_history = json.dumps(reg_obj.get('history', []))
    
    operator = profile.get('operator')
    owner = None # Not in profile
    
    counters = profile.get('counters', {})
    tsn = counters.get('tsn')
    csn = counters.get('csn')
    tsn_conf = counters.get('confidence')
    csn_conf = counters.get('confidence')
    
    state = profile.get('state')
    dossier_date = profile.get('dossier_date')
    profile_json_str = json.dumps(profile)
    
    # ID resolution
    fallback_id = primary_serial or msn or esn or reg or "unknown"
    asset_id = f"asset::{asset_class}::{fallback_id}"
    
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    
    conn.execute('''
       INSERT INTO assets (id, asset_kind, subtype, type_designation, tcds, yom,
           msn, registration, registration_history, operator, owner, primary_serial,
           state, tsn, csn, tsn_confidence, csn_confidence, dossier_date, profile_json)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (asset_id, asset_class, subtype, type_designation, tcds, yom,
          msn, reg, reg_history, operator, owner, primary_serial,
          state, tsn, csn, tsn_conf, csn_conf, dossier_date, profile_json_str))
          
    # Aggregate from corpus
    cur = conn.cursor()
    cur.execute("SELECT serial_numbers, mis_system, date FROM pages")
    rows = cur.fetchall()
    
    sns = Counter()
    mis_systems = Counter()
    dates = []
    
    for r in rows:
        sn_list_str = r[0]
        if sn_list_str:
            try:
                for sn in json.loads(sn_list_str):
                    sns[sn] += 1
            except: pass
            
        mis = r[1]
        if mis: mis_systems[mis] += 1
        
        d = r[2]
        if d: dates.append(d)
        
    dates.sort()
    corpus_date = dates[-1] if dates else None
    corpus_mis = mis_systems.most_common(1)[0][0] if mis_systems else None
    
    # Very basic finding for mismatches - in reality we'd extract entities and compare
    # but entities are already handled via the previous parsing scripts
    corpus_msn = sns.most_common(1)[0][0] if sns else None
    
    # Also check context discrepancy from pages
    cur.execute("SELECT id, context_discrepancy FROM pages WHERE context_discrepancy IS NOT NULL AND context_discrepancy != ''")
    disc_pages = cur.fetchall()
    
    for pid, disc in disc_pages:
        # In a real impl we'd insert into a findings table, but there is no findings table created yet 
        # based on schema.sql? Oh, schema.sql has findings. Wait. Let's create findings table if not exists.
        # But tools.py executed the whole schema.sql. Yes.
        
        # We need a new id for findings
        fid = f"finding::{pid}::CONTEXT_DISCREPANCY"
        conn.execute('''
            INSERT OR IGNORE INTO findings 
            (id, finding_type, severity, title, component_id, source_page_id, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (fid, 'CONTEXT_DISCREPANCY', 'L2', 'Context discrepancy reported by OCR', asset_id, pid, str(disc), 'OPEN'))
    
    conn.commit()
    
    # Check assets table for verification
    cur.execute("SELECT 'assets' AS t, COUNT(*) AS n FROM assets")
    n_assets = cur.fetchone()[1]
    
    cur.execute("SELECT id, asset_kind, type_designation, msn, registration, profile_json, dossier_date FROM assets")
    asset_row = cur.fetchone()
    
    cur.execute("SELECT COUNT(*) FROM findings WHERE finding_type='CONTEXT_DISCREPANCY'")
    n_disc = cur.fetchone()[0]
    
    with open(log_path, "a") as f:
        f.write("\n== Phase 2 verification ==\n")
        f.write("field            profile          corpus_majority   verdict\n")
        f.write("---------------- ---------------- ----------------- -------\n")
        
        match_msn = "match" if fuzzy_match(msn, corpus_msn) else "mismatch"
        msn_str = str(msn)[:15].ljust(16)
        corpus_msn_str = str(corpus_msn)[:15].ljust(17)
        f.write(f"msn              {msn_str} {corpus_msn_str} {match_msn}\n")
        
        f.write("\n")
        f.write(f"- count(assets)                       : {n_assets}\n")
        f.write(f"- assets.asset_kind populated         : {'yes' if asset_row and asset_row[1] else 'no'}\n")
        f.write(f"- assets.profile_json IS NOT NULL     : {'yes' if asset_row and asset_row[5] else 'no'}\n")
        f.write(f"- assets.dossier_date populated       : {'yes' if asset_row and asset_row[6] else 'no'}\n")
        f.write(f"- count(findings WHERE finding_type='CONTEXT_DISCREPANCY') logged : {n_disc}\n")

if __name__ == "__main__":
    main()
