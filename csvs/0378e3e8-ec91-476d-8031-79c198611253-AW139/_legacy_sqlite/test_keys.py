import json
data = json.load(open('graph_export.json'))

required_keys = ['asset', 'stats', 'nodes', 'edges', 'events', 'findings',
                 'findings_summary', 'doc_nodes', 'doc_edges', 'ata_nodes',
                 'ata_edges', 'time_nodes', 'time_edges', 'lease_return_state', 'priority_items']

for k in required_keys:
    if k not in data:
        print(f'missing {k}')
        
# Check findings empty condition from OVERVIEW / Phase 10
findings = data.get('findings', {})
if not findings:
    print('findings is empty!')
else:
    print(f'findings has {len(findings)} keys')
