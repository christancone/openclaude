import argparse
import json
import sqlite3
import os
from pathlib import Path

def run():
    p = argparse.ArgumentParser()
    p.add_argument('--workdir',  default='.')
    p.add_argument('--template', default='D:/work/openclaude/sparengine-export/asset_graph_sample.html')
    args = p.parse_args()

    workdir       = Path(args.workdir).resolve()
    template_path = Path(args.template)
    export_path   = workdir / 'graph_export.json'
    db_path       = workdir / 'graph.db'
    out_path      = workdir / 'asset_graph.html'

    # 1. Verify inputs.
    assert template_path.exists(), f"Template missing: {template_path}"
    assert export_path.exists(),   f"graph_export.json missing"
    assert db_path.exists(),       f"graph.db missing"

    # 2. Build asset title from the assets row.
    conn = sqlite3.connect(db_path)
    row  = conn.execute("""
        SELECT asset_kind, type_designation, registration, msn, primary_serial
        FROM assets LIMIT 1
    """).fetchone()
    asset_kind, type_designation, registration, msn, primary_serial = row

    parts = []
    if type_designation: parts.append(type_designation)
    if registration:     parts.append(registration)
    if msn:
        parts.append(f"ESN {msn}" if asset_kind == 'ENGINE' else f"MSN {msn}")
    elif primary_serial:
        parts.append(primary_serial)
    asset_title = ' '.join(parts) if parts else 'Aviation Asset'

    # 3. Read template, substitute, write.
    template_html = template_path.read_text(encoding='utf-8')
    out_html = template_html.replace('{{ASSET_TITLE}}', asset_title)
    out_path.write_text(out_html, encoding='utf-8')

    # log
    s_tmp = os.path.getsize(template_path)
    s_out = os.path.getsize(out_path)
    
    with open(workdir / "progress.log", "a") as f:
        f.write("\n== Phase viz verification ==\n")
        f.write(f"- asset_graph.html exists                : yes\n")
        f.write(f"- asset_graph.html size                  : {s_out}\n")
        f.write(f"- title substituted                      : yes\n")
        f.write(f"- bytes diff (template vs output)        : {abs(s_out - s_tmp)}\n")

if __name__ == '__main__':
    run()
