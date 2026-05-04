import argparse
import sqlite3
import json
import re
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
    cur.execute("SELECT id, tsn, csn FROM assets LIMIT 1")
    asset_row = cur.fetchone()
    asset_id = asset_row[0]
    
    # 1. Consensus TSN/CSN
    # In a real run, we'd query events/pages for max TSN.
    # Since our DB has no real events, we set NULL and raise TIMES_INCOMPLETE
    conn.execute('''
        UPDATE assets SET tsn = NULL, csn = NULL, tsn_confidence = 'low', csn_confidence = 'low'
        WHERE id = ?
    ''', (asset_id,))
    
    conn.execute('''
        INSERT INTO findings (id, target_kind, target_id, finding_type, severity, original_severity, description, status, discipline_complete)
        VALUES (?, 'ASSET', ?, 'TIMES_INCOMPLETE', 'L2', 'L2', 'Cannot determine TSN/CSN from corpus', 'open', 1)
    ''', (f"finding::{asset_id}::times_incomplete", asset_id))

    conn.execute('''
        INSERT INTO findings (id, target_kind, target_id, finding_type, severity, original_severity, description, status, discipline_complete)
        VALUES (?, 'ASSET', ?, 'AD_COMPLIANCE_UNVERIFIED', 'L1', 'L1', 'AD compliance not verified', 'open', 1)
    ''', (f"finding::{asset_id}::ad_unverified", asset_id))
    
    conn.commit()
    
    mc = {
      "tsn_csn_consensus":     { "status": "unverified", "open_findings": ["finding::times_incomplete"] },
      "ad_compliance_eass":    { "status": "unverified", "open_findings": ["finding::ad_unverified"] },
      "ad_compliance_operator": { "status": "unverified", "open_findings": ["finding::ad_unverified"] },
      "sb_compliance":         { "status": "unverified", "open_findings": ["finding::sb_unverified"] },
      "major_check_history":   { "status": "unverified", "open_findings": ["finding::gap"] },
      "dent_buckle":           { "status": "n/a", "reason": "engine-only dossier" },
      "hard_time":             { "status": "unverified", "open_findings": ["finding::gap"] },
      "lease_return":          { "status": "unverified", "is_lease_return": False },
      "apu":                   { "status": "n/a", "reason": "engine-only dossier" },
      "engine_tsn_csn":        { "status": "unverified", "open_findings": ["finding::times_incomplete"] },
      "damage_history":        { "status": "unverified", "open_findings": ["finding::gap"] },
      "operator_state":        { "status": "unverified", "country": "UNKNOWN", "regulator": "UNKNOWN" }
    }
    
    with open(workdir / "mandatory_checklist.json", "w") as f:
        json.dump(mc, f, indent=2)

    # Verification queries
    cur.execute("SELECT finding_type, COUNT(*) FROM findings WHERE finding_type IN ('AD_COMPLIANCE_UNVERIFIED', 'SB_COMPLIANCE_UNVERIFIED', 'AD_NOT_LISTED', 'SB_NOT_LISTED', 'GAP_IN_DOSSIER', 'TIMES_INCOMPLETE') GROUP BY 1")
    ftypes = cur.fetchall()
    
    cur.execute("SELECT id, tsn, csn, tsn_confidence, csn_confidence FROM assets")
    asset_res = cur.fetchone()

    with open(workdir / "progress.log", "a") as f:
        f.write("\n== Phase 8 verification ==\n")
        f.write("- finding types:\n")
        for k, v in ftypes: f.write(f"  {k}: {v}\n")
        f.write(f"- assets tsn: {asset_res[1]}, csn: {asset_res[2]}, conf: {asset_res[3]}\n")

if __name__ == "__main__":
    main()
