import argparse
import pandas as pd
import json
from pathlib import Path
import orjson
import sys
from collections import Counter
import re

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
            except:
                continue
                
            content = extracted_json.get('content', {})
            doc_type = content.get('document_type', '')
            orig_path = str(row.get('original_path', '')).lower()
            
            # Heuristics
            if len(selected_pages) < 5:
                selected_pages.append((row, extracted_json))
            elif doc_type in ['certificate_of_airworthiness', 'certificate_of_registration', 'airworthiness_review_certificate', 'airframe_logbook', 'engine_logbook', 'engine_llp_status_sheet', 'life_limited_parts_status', 'weight_and_balance_report']:
                selected_pages.append((row, extracted_json))
            elif any(x in orig_path for x in ["redelivery", "lease return", "preservation", "cover"]):
                selected_pages.append((row, extracted_json))
                
        if len(selected_pages) >= 30:
            break

    # If we still have fewer than 30, add some more
    if len(selected_pages) < 30:
         df_iter = pd.read_csv(csv_path, chunksize=100)
         for chunk in df_iter:
            for idx, row in chunk.iterrows():
                if len(selected_pages) >= 30:
                    break
                if any(r['id'] == row['id'] for r, _ in selected_pages):
                    continue
                try:
                    selected_pages.append((row, orjson.loads(row['extracted_json'])))
                except:
                    pass
            if len(selected_pages) >= 30:
                break

    # Sort to try and find most recent dates
    # (Simplified date sorting for heuristic purposes)

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
    asset_class = "fixed_wing"
    subtype = "FIXED_WING_JET"
    has_helicopter_signs = False
    has_engine_only_signs = False
    has_apu_signs = False

    for row, ext in selected_pages:
        content = ext.get('content', {})
        ents = content.get('entities', [])
        entities_count += len(ents)
        
        for e in ents:
            if e.get('confidence') in ['high', 'medium']:
                val = e.get('value', '')
                if not val: continue
                
                etype = e.get('entity_type')
                if etype == 'registration': regs[val] += 1
                elif etype == 'msn': msns[val] += 1
                elif etype == 'esn': esns[val] += 1
                elif etype == 'type_designation': type_designations[val] += 1
                elif etype == 'operator': operators[val] += 1
                elif etype == 'mro': mros[val] += 1
                
        meta = content.get('metadata', {})
        if meta.get('mis_system'):
            mis_systems[meta.get('mis_system')] += 1
            
        mdates = meta.get('dates', [])
        for d in mdates:
            if re.match(r'^\d{4}-\d{2}-\d{2}$', d):
                dates.append(d)
                
        # Helicopter detection
        for e in ents:
            v = str(e.get('value','')).upper()
            if any(x in v for x in ['MGB', 'IGB', 'TGB', 'MAIN ROTOR', 'TAIL ROTOR', 'SWASHPLATE']):
                has_helicopter_signs = True

    current_reg = regs.most_common(1)[0][0] if regs else None
    msn = msns.most_common(1)[0][0] if msns else None
    type_desig = type_designations.most_common(1)[0][0] if type_designations else None
    operator = operators.most_common(1)[0][0] if operators else None
    
    if has_helicopter_signs:
        asset_class = "rotorcraft"
        subtype = "HELICOPTER"
    elif not regs and not msns and esns:
        asset_class = "engine"
        subtype = "TURBOFAN" # Guess, ideally refine
        has_engine_only_signs = True
        
    dates.sort()
    dossier_date = dates[-1] if dates else None
    
    expected_tiers = []
    if asset_class == "engine":
        expected_tiers = ["ENGINE"]
    else:
        expected_tiers = ["ENGINE","LANDING_GEAR","PROPELLER","AIRFRAME","AVIONICS"]
        if has_helicopter_signs:
            expected_tiers.extend(["ROTOR_SYSTEM", "TRANSMISSION"])
        # APU if requested/detected - assume true for now for jets
        if subtype == "FIXED_WING_JET":
            expected_tiers.append("APU")
            
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
        f.write(f"- type_designation derived from CSV (not folder name): {'yes' if type_desig else 'no'}\n")
        f.write(f"- expected_tiers contains at least one tier           : {'yes' if expected_tiers else 'no'}\n")
        f.write(f"- pages_read_in_phase0                                : {len(selected_pages)}\n")
        f.write(f"- entities_aggregated                                 : {entities_count}\n")
        f.write("\nProfile contents:\n")
        f.write(json.dumps(profile, indent=2))
        f.write("\n")

if __name__ == "__main__":
    main()
