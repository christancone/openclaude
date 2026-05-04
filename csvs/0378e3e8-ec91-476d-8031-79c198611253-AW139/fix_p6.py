with open('phase6.py', 'r') as f:
    text = f.read()

text = text.replace("INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind, edge_type, confidence, evidence_file, evidence_page)\n                VALUES (?, ?, 'STAMP'",
"INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind, edge_type, confidence, evidence_file, evidence_page)\n                VALUES (?, ?, 'STAMP', ?, 'PAGE', 'STAMP_BINDS_TO', 'high', 'database', 0)\n            ''', (f'edge::{s_id}::{pid}', s_id, pid))")

with open('phase6.py', 'w') as f:
    f.write(text)
