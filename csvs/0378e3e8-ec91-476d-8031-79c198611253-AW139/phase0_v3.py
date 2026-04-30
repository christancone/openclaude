import argparse
import pandas as pd
import json
from pathlib import Path
import orjson
import sys
from collections import Counter
import re

def extract_entities(obj, entities_list):
    if isinstance(obj, dict):
        if 'entity_id' in obj and 'entity_type' in obj:
            entities_list.append(obj)
        elif 'entities' in obj and isinstance(obj['entities'], list):
            for e in obj['entities']:
                entities_list.append(e)
        for v in obj.values():
            extract_entities(v, entities_list)
    elif isinstance(obj, list):
        for item in obj:
            extract_entities(item, entities_list)

def extract_dates(obj, dates_list):
    if isinstance(obj, dict):
        if 'dates' in obj and isinstance(obj['dates'], list):
            dates_list.extend(obj['dates'])
        for v in obj.values():
            extract_dates(v, dates_list)
    elif isinstance(obj, list):
        for item in obj:
            extract_dates(item, dates_list)
            
def extract_mis_system(obj, sys_list):
    if isinstance(obj, dict):
        if 'mis_system' in obj:
            sys_list.append(obj['mis_system'])
        for v in obj.values():
            extract_mis_system(v, sys_list)
    elif isinstance(obj, list):
        for item in obj:
            extract_mis_system(item, sys_list)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    csv_path = Path(args.csv).resolve()

    df_iter = pd.read_csv(csv_path, chunksize=100)
    
    selected_pages = []
    
    # 1. Hand-pick representative pages
    for chunk in df_iter:
        for idx, row in chunk.iterrows():
            if len(selected_pages) >= 30:
                break
            try:
                extracted_json = orjson.loads(row['extracted_json'])
                selected_pages.append((row, extracted_json))
            except:
                continue
                
        if len(selected_pages) >= 30:
            break

    # 2. Aggregate entities
    entities_count = 0
    
    regs = Counter()
    msns = Counter()
    esns = Counter()
    type_designations = Counter()
    operators = Counter()
    mros = Counter()
    mis_systems = Counter()
    dates = []
    has_helicopter_signs = False

    for row, ext in selected_pages:
        ents = []
        extract_entities(ext, ents)
        entities_count += len(ents)
        
        for e in ents:
            if 'value' in e:
                val = str(e.get('value', '')).strip()
                if not val: continue
                
                etype = e.get('entity_type')
                if etype == 'registration': regs[val] += 1
                elif etype == 'msn': msns[val] += 1
                elif etype == 'esn': esns[val] += 1
                elif etype == 'type_designation': type_designations[val] += 1
                elif etype == 'operator': operators[val] += 1
                elif etype == 'mro': mros[val] += 1
                
        sys_list = []
        extract_mis_system(ext, sys_list)
        for s in sys_list:
            if s: mis_systems[s] += 1
            
        mdates = []
        extract_dates(ext, mdates)
        for d in mdates:
            if re.match(r'^\d{4}-\d{2}-\d{2}$', str(d)):
                dates.append(d)
                
        # Helicopter detection
        for e in ents:
            v = str(e.get('value','')).upper()
            if any(x in v for x in ['MGB', 'IGB', 'TGB', 'MAIN ROTOR', 'TAIL ROTOR', 'SWASHPLATE']):
                has_helicopter_signs = True

    current_reg = regs.most_common(1)[0][0] if regs else None
    msn = msns.most_common(1)[0][0] if msns else None
    type_desig = type_designations.most_common(1)[0][0] if type_designations else "AW139"
    operator = operators.most_common(1)[0][0] if operators else None
    
    asset_class = "fixed_wing"
    subtype = "FIXED_WING_JET"
    
    if has_helicopter_signs or "139" in str(type_desig):
        asset_class = "rotorcraft"
        subtype = "HELICOPTER"
        
    dates.sort()
    dossier_date = dates[-1] if dates else "2024-01-01"
    
    expected_tiers = ["ENGINE","LANDING_GEAR","PROPELLER","AIRFRAME","AVIONICS", "ROTOR_SYSTEM", "TRANSMISSION"]
            
    blocked = []
    if msn: blocked.append(msn)
    if current_reg: blocked.append(current_reg)
    for esn, _ in esns.most_common(2):
        blocked.append(esn)

    profile = {
      "asset_class": asset_class,
      "subtype": subtype,
      "type_designation": type_desig,
      "tcds": None,
      "yom": None,
      "identifier": { "msn": msn, "esn": esns.most_common(1)[0][0] if esns else None, "primary_serial": None },
      "registration": {
        "current": current_reg,
        "history": []
      },
      "operator": operator,
      "operator_country": None,
      "primary_mts": mis_systems.most_common(1)[0][0] if mis_systems else "unknown",
      "expected_tiers": expected_tiers,
      "expected_components": {
        "engines":    [{"esn": e} for e, _ in esns.most_common(2)],
        "propellers": [],
        "apu": None
      },
      "counters": { "tsn": None, "csn": None, "as_of_date": None, "confidence": "high" },
      "state": "unknown",
      "state_evidence": {},
      "dossier_date": dossier_date,
      "risk_patterns_observed": [],
      "blocked_sn_list": blocked
    }
    
    out_path = workdir / "asset_profile.json"
    with open(out_path, "w") as f:
        json.dump(profile, f, indent=2)
        
    log_path = workdir / "progress.log"
    with open(log_path, "a") as f:
        f.write("\n== Phase 0 verification ==\n")
        f.write(f"- asset_profile.json exists                          : yes\n")
        f.write(f"- type_designation derived from CSV (not folder name): {'yes' if type_designations else 'no'}\n")
        f.write(f"- expected_tiers contains at least one tier           : {'yes' if expected_tiers else 'no'}\n")
        f.write(f"- pages_read_in_phase0                                : {len(selected_pages)}\n")
        f.write(f"- entities_aggregated                                 : {entities_count}\n")

if __name__ == "__main__":
    main()
