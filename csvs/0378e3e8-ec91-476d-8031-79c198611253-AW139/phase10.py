import argparse
import sqlite3
import json
from pathlib import Path
import os

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    db_path = workdir / "graph.db"
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Init dict
    export = {}
    
    # Asset
    cur.execute("SELECT * FROM assets LIMIT 1")
    row = cur.fetchone()
    export['asset'] = dict(row) if row else {'id': 'asset::fallback'}
    
    cur.execute("SELECT (SELECT COUNT(*) FROM pages) AS total_pages, (SELECT COUNT(*) FROM documents) AS total_documents, (SELECT COUNT(*) FROM components) AS total_components, (SELECT COUNT(*) FROM events) AS total_events, (SELECT COUNT(*) FROM stamps) AS total_stamps, (SELECT COUNT(*) FROM findings WHERE status='open') AS total_findings_open")
    row = cur.fetchone()
    stats = dict(row) if row else {}
    
    cur.execute("SELECT tier, COUNT(*) as c FROM components GROUP BY tier")
    stats['components_by_tier'] = {r['tier']: r['c'] for r in cur.fetchall()}
    cur.execute("SELECT status, COUNT(*) as c FROM components GROUP BY status")
    stats['components_by_status'] = {r['status']: r['c'] for r in cur.fetchall()}
    cur.execute("SELECT document_type, COUNT(*) as c FROM documents GROUP BY document_type")
    stats['documents_by_type'] = {r['document_type']: r['c'] for r in cur.fetchall()}
    cur.execute("SELECT evidentiary_weight, COUNT(*) as c FROM pages GROUP BY evidentiary_weight")
    stats['evidentiary_weight_breakdown'] = {r['evidentiary_weight']: r['c'] for r in cur.fetchall()}
    cur.execute("SELECT severity, COUNT(*) as c FROM findings WHERE status='open' GROUP BY severity")
    stats['findings_by_severity'] = {r['severity']: r['c'] for r in cur.fetchall()}
    export['stats'] = stats
    
    # Nodes
    nodes = []
    nodes.append({'id': export['asset']['id'], 'tier': 'AIRCRAFT_CENTER', 'shape': 'star', 'size': 40, 'color': '#4CAF50'})
    
    with open(workdir / "asset_profile.json", "r") as f:
        profile = json.load(f)
    for t in profile.get('expected_tiers', []):
        nodes.append({'id': f"tier::{t}", 'status': 'TIER_GROUP', 'shape': 'dot', 'size': 30})
        
    cur.execute("SELECT id, tier, status, canonical_pn, installed_sn FROM components")
    for r in cur.fetchall():
        nodes.append({
            'id': r['id'], 'tier': r['tier'], 'status': r['status'],
            'data': {'canonical_pn': r['canonical_pn'], 'installed_sn': r['installed_sn'], 'is_llp': 0}
        })
    export['nodes'] = nodes
    
    # Edges
    edges = []
    # Include the edge types we actually have so they get exported
    cur.execute("SELECT id, source_id AS \"from\", target_id AS \"to\", edge_type, confidence, evidence_quote AS title FROM edges WHERE edge_type IN ('HAS_TIER', 'BELONGS_TO_TIER', 'PART_OF', 'INSTALLED_ON', 'INSTALLATION', 'REMOVAL', 'OVERHAUL', 'INSPECTION', 'SHOP_VISIT', 'SB_COMPLIANCE', 'AD_COMPLIANCE', 'PART_REPLACED', 'RELEASE_TO_SERVICE', 'AFFECTED_BY', 'DOCUMENTED_IN', 'HAS_EVENT', 'PAGE_REFERENCES', 'ASSIGNED_ATA', 'PART_OF_WORK_ORDER')")
    for r in cur.fetchall():
        edges.append(dict(r))
    export['edges'] = edges
    
    events = {}
    cur.execute("SELECT component_id, id AS event_id, event_type, event_date, description, task_compliance_status, compliance_status_reason, work_order_id, work_package_id, mro FROM events ORDER BY component_id, event_date")
    for r in cur.fetchall():
        cid = r['component_id']
        cid = str(cid) if cid else 'unknown'
        if cid not in events: events[cid] = []
        events[cid].append(dict(r))
    export['events'] = events
    
    # Findings
    findings = {}
    cur.execute("SELECT target_id AS component_id, id, finding_type, severity, original_severity, severity_downgrade_reason, description, what_auditor_needs, file_name, page_index, status, verification_strategy FROM findings WHERE status = 'open' AND target_kind = 'COMPONENT' ORDER BY target_id, severity")
    for r in cur.fetchall():
        cid = r['component_id']
        cid = str(cid) if cid else 'unknown'
        if cid not in findings: findings[cid] = []
        findings[cid].append(dict(r))
    export['findings'] = findings
    
    # Summaries
    try:
        with open(workdir / "findings_summary.json", "r") as f:
            export['findings_summary'] = json.load(f)
    except:
        export['findings_summary'] = {}
        
    try:
        with open(workdir / "mandatory_checklist.json", "r") as f:
            export['mandatory_checklist'] = json.load(f)
    except:
        export['mandatory_checklist'] = {}
        
    try:
        with open(workdir / "verification_stats.json", "r") as f:
            export['verification_stats'] = json.load(f)
    except:
        export['verification_stats'] = {}
        
    # Doc nodes/edges
    doc_nodes = []
    cur.execute("SELECT id, document_type, file_name FROM documents")
    for r in cur.fetchall(): doc_nodes.append(dict(r))
    export['doc_nodes'] = doc_nodes
    
    doc_edges = []
    cur.execute("SELECT id, source_id AS \"from\", target_id AS \"to\", edge_type FROM edges WHERE source_kind = 'DOCUMENT' OR target_kind = 'DOCUMENT' OR source_kind = 'PAGE' OR target_kind = 'PAGE'")
    for r in cur.fetchall(): doc_edges.append(dict(r))
    export['doc_edges'] = doc_edges
    
    # ATA
    ata_nodes = []
    cur.execute("SELECT id, chapter_number, title FROM ata_chapters")
    for r in cur.fetchall(): ata_nodes.append(dict(r))
    export['ata_nodes'] = ata_nodes
    
    ata_edges = []
    cur.execute("SELECT id, source_id AS \"from\", target_id AS \"to\", edge_type FROM edges WHERE edge_type = 'ASSIGNED_ATA' OR edge_type = 'PAGE_REFERENCES'")
    for r in cur.fetchall(): ata_edges.append(dict(r))
    export['ata_edges'] = ata_edges
    
    # Time
    time_nodes = []
    time_edges = []
    export['time_nodes'] = time_nodes
    export['time_edges'] = time_edges
    
    # Lease return
    cur.execute("SELECT * FROM lease_return_state")
    row = cur.fetchone()
    export['lease_return_state'] = dict(row) if row else {}
    
    # Priority items
    priority_items = []
    cur.execute("SELECT pi.*, c.canonical_pn || ' (' || c.installed_sn || ')' AS component_label FROM priority_items pi LEFT JOIN components c ON c.id = pi.component_id ORDER BY pi.rank")
    for r in cur.fetchall(): priority_items.append(dict(r))
    export['priority_items'] = priority_items
    
    out_path = workdir / "graph_export.json"
    with open(out_path, "w") as f:
        json.dump(export, f, indent=2)

    with open(workdir / "progress.log", "a") as f:
        f.write("\n== Phase 10 verification ==\n")
        f.write(f"- graph_export.json size                        : {os.path.getsize(out_path)}\n")
        f.write(f"- nodes count                                   : {len(nodes)}\n")
        f.write(f"- edges count                                   : {len(edges)}\n")
        f.write(f"- events keyed by component                     : {len(events.keys())}\n")
        f.write(f"- findings keyed by component                   : {len(findings.keys())}\n")
        f.write(f"- mandatory_checklist items present             : {len(export['mandatory_checklist'].keys())}\n")

if __name__ == "__main__":
    main()
