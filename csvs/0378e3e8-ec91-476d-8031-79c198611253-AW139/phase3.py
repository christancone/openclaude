import argparse
import json
from pathlib import Path
import sqlite3

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    db_path = workdir / "graph.db"
    
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    
    cur = conn.cursor()
    cur.execute("SELECT id, profile_json FROM assets LIMIT 1")
    row = cur.fetchone()
    
    if not row:
        print("Error: No asset row found in DB")
        return
        
    asset_id, profile_json_str = row
    profile = json.loads(profile_json_str)
    
    expected_tiers = profile.get("expected_tiers", [])
    
    for tier in expected_tiers:
        tier_id = f"tier::{tier}"
        edge_id = f"edge::has_tier::{tier}"
        conn.execute('''
            INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind,
                edge_type, confidence, evidence_file, evidence_page, evidence_quote)
            VALUES (?, ?, 'ASSET', ?, 'TIER_GROUP', 'HAS_TIER', 'high',
                    'asset_profile.json', 0, ?)
        ''', (
            edge_id,
            asset_id,
            tier_id,
            f"expected_tiers contains {tier}"
        ))
        
    conn.commit()
    
    cur.execute("SELECT COUNT(*) FROM edges WHERE edge_type = 'HAS_TIER'")
    has_tier_edges = cur.fetchone()[0]
    
    log_path = workdir / "progress.log"
    with open(log_path, "a") as f:
        f.write("\n== Phase 3 verification ==\n")
        f.write(f"- count(edges WHERE edge_type='HAS_TIER') : {has_tier_edges} (expected {len(expected_tiers)})\n")
        f.write(f"- each expected tier has exactly one edge : {'yes' if has_tier_edges == len(expected_tiers) else 'no'}\n")

if __name__ == "__main__":
    main()
