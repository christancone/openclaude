code = """import argparse
import json
import sqlite3
import pandas as pd
from pathlib import Path
from collections import defaultdict, Counter

def normalize_pn(pn):
    return str(pn).strip().upper()

def normalize_sn(sn):
    return str(sn).strip().upper()

def get_tier_from_ata(ata):
    try:
        a = int(str(ata)[:2])
        if 70 <= a <= 84: return "ENGINE"
        if a == 61: return "PROPELLER"
        if a == 32: return "LANDING_GEAR"
        if 62 <= a <= 67: return "ROTOR_SYSTEM" # Rough helicopter mapping
        if a in [51, 52, 53, 54, 55, 56, 57]: return "AIRFRAME"
        if a in [22, 23, 27, 31, 33, 34, 42]: return "AVIONICS"
    except: pass
    return "UNKNOWN"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    db_path = workdir / 'graph.db'
    log_path = workdir / 'progress.log'
    profile_path = workdir / 'asset_profile.json'
    
    with open(profile_path, 'r') as f:
        profile = json.load(f)
        
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')
    
    asset_id = conn.execute('SELECT id FROM assets LIMIT 1').fetchone()[0]
    
    # 1. Seed list first
    seeded_count = 0
    exp_comps = profile.get('expected_components', {})
    engines = exp_comps.get('engines', [])
    
    # Track to avoid duplicates
    seen_components = set()
    
    for idx, e in enumerate(engines):
        esn = e.get('esn')
        if not esn: continue
        
        pn = 'UNKNOWN_ENGINE_PN'
        sn = normalize_sn(esn)
        comp_id = f"component::{pn}::{sn}"
        
        if comp_id not in seen_components:
            conn.execute("INSERT OR IGNORE INTO part_types (canonical_pn, description, ata_chapter, is_llp, is_overhaul) VALUES (?, ?, ?, ?, ?)", (pn, 'ENGINE_SEED', '72', 0, 1))
            
            conn.execute("INSERT OR IGNORE INTO serials (id, canonical_pn, serial_number, description, installed_status) VALUES (?, ?, ?, ?, ?)", (f"{pn}::{sn}", pn, sn, 'Engine seed', 'ON'))
            
            conn.execute("INSERT OR IGNORE INTO components (id, asset_id, canonical_pn, installed_sn, description, tier, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (comp_id, asset_id, pn, sn, 'Expected Engine', 'ENGINE', 'DISCOVERED'))
            seen_components.add(comp_id)
            seeded_count += 1

    # 2. Extract pairs from pages
    
    cur = conn.cursor()
    cur.execute("SELECT id, document_type, part_numbers, serial_numbers, ata_chapters FROM pages WHERE part_numbers != '[]' OR serial_numbers != '[]'")
    rows = cur.fetchall()
    
    pairs = Counter()
    page_hits = defaultdict(list)
    comp_meta = {}
    
    blocked = set([str(b).strip().upper() for b in profile.get('blocked_sn_list', []) if b])
    
    for pid, doc_type, pns_str, sns_str, ata_str in rows:
        try: pns = json.loads(pns_str)
        except: pns = []
        try: sns = json.loads(sns_str)
        except: sns = []
        try: atas = json.loads(ata_str)
        except: atas = []
        
        valid_sns = [sn for sn in sns if normalize_sn(sn) not in blocked and not str(sn).startswith('199') and not str(sn).startswith('20')]
        
        if not pns and valid_sns and doc_type in ['engine_llp_status_sheet', 'life_limited_parts_status']:
            pns = ['UNKNOWN_LLP_PN']
            
        for pn in pns:
            npn = normalize_pn(pn)
            for sn in valid_sns:
                nsn = normalize_sn(sn)
                pair = (npn, nsn)
                pairs[pair] += 1
                page_hits[pair].append((pid, doc_type))
                
                if pair not in comp_meta:
                    comp_meta[pair] = {
                        'ata': atas[0] if atas else None,
                        'is_llp': 0,
                        'is_overhaul': 0,
                        'tier': 'UNKNOWN'
                    }
                
                if doc_type in ['engine_llp_status_sheet', 'life_limited_parts_status']:
                    comp_meta[pair]['is_llp'] = 1
                if 'overhaul' in doc_type.lower():
                    comp_meta[pair]['is_overhaul'] = 1
                    
                if atas:
                    comp_meta[pair]['ata'] = atas[0]
                    comp_meta[pair]['tier'] = get_tier_from_ata(atas[0])
                    
    promoted = 0
    for (pn, sn), count in pairs.items():
        meta = comp_meta[(pn, sn)]
        tier = meta['tier']
        
        high_val = tier in ['ENGINE', 'LANDING_GEAR', 'PROPELLER', 'ROTOR_SYSTEM', 'TRANSMISSION', 'APU']
        low_val = tier in ['AVIONICS', 'SYSTEMS', 'INTERIOR']
        
        promote = False
        if high_val and count >= 1: promote = True
        elif low_val and count >= 2: promote = True
        elif tier == 'AIRFRAME' and count >= 2: promote = True
        elif count >= 1 and (meta['is_llp'] or meta['is_overhaul']): promote = True
        elif count >= 3: promote = True
        
        if promote:
            comp_id = f"component::{pn}::{sn}"
            if comp_id not in seen_components:
                conn.execute("INSERT OR IGNORE INTO part_types (canonical_pn, description, ata_chapter, is_llp, is_overhaul) VALUES (?, ?, ?, ?, ?)", (pn, f'Discovered PN {pn}', meta['ata'], meta['is_llp'], meta['is_overhaul']))
                
                conn.execute("INSERT OR IGNORE INTO serials (id, canonical_pn, serial_number, description, installed_status) VALUES (?, ?, ?, ?, ?)", (f"{pn}::{sn}", pn, sn, f'Discovered SN {sn}', 'ON'))
                
                conn.execute("INSERT OR IGNORE INTO components (id, asset_id, canonical_pn, installed_sn, description, tier, status, is_llp, is_overhaul) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (comp_id, asset_id, pn, sn, f'Component {pn}/{sn}', tier, 'DISCOVERED', meta['is_llp'], meta['is_overhaul']))
                
                seen_components.add(comp_id)
                promoted += 1

    conn.commit()
    
    cur.execute("SELECT COUNT(*) FROM part_types")
    n_part_types = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM serials")
    n_serials = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM components")
    n_components = cur.fetchone()[0]
    
    cur.execute("SELECT tier, COUNT(*) FROM components GROUP BY tier")
    tier_counts = {r[0]: r[1] for r in cur.fetchall()}
    
    cur.execute("SELECT is_llp, COUNT(*) FROM components GROUP BY is_llp")
    llp_counts = {r[0]: r[1] for r in cur.fetchall()}
    
    with open(log_path, 'a') as f:
        f.write("\n== Phase 4 verification ==\n")
        f.write(f"- count(components)             : {n_components}\n")
        f.write(f"- count(part_types)             : {n_part_types}\n")
        f.write(f"- count(serials)                : {n_serials}\n")
        f.write(f"- components grouped by tier    : {json.dumps(tier_counts)}\n")
        f.write(f"- LLP count                     : {json.dumps(llp_counts)}\n")
        f.write(f"- count(components seeded from profile) : {seeded_count}\n")

if __name__ == '__main__':
    main()
"""
with open('phase4.py', 'w') as f:
    f.write(code)
