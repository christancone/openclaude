# PHASE viz — Generate `asset_graph.html` (panel-only)

**Intent.** Copy `asset_graph_template_panels.html` from `sparengine-export/`, substitute the asset title, write `asset_graph.html` next to `graph_export.json`. **String substitution only.** No graph-traversal HTML embedding — the panel HTML fetches `graph_export.json` at runtime.

The graph viz proper lives in **Neo4j Browser** at `http://localhost:7474`. The panel HTML carries only the four audit panels (Critical Items, Mandatory Checklist, Audit Quality, Lease-return banner).

---

## What this phase produces

`asset_graph.html` in the workdir — a self-contained HTML file with embedded JS that fetches `graph_export.json` (sibling file) and renders panels.

Total size: ~9 KB (the template itself).

---

## Steps

```python
import json
from pathlib import Path

workdir = Path(args.workdir).resolve()

# Locate the template — walk up to find sparengine-export/
for parent in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
    cand = parent / "sparengine-export" / "asset_graph_template_panels.html"
    if cand.is_file():
        template_path = cand
        break
else:
    raise FileNotFoundError("phase_viz: template not found")

# Pick a title from graph_export.json (Phase 10 wrote it)
title = "Sparengine asset"
export_json = workdir / "graph_export.json"
if export_json.exists():
    data = json.loads(export_json.read_text(encoding="utf-8"))
    asset = data.get("asset") or {}
    title = (asset.get("name") or asset.get("registration") or
             asset.get("msn") or asset.get("value") or title)

template = template_path.read_text(encoding="utf-8")
html = template.replace("{{ASSET_TITLE}}", str(title))

(workdir / "asset_graph.html").write_text(html, encoding="utf-8")
```

That's it. No graph traversal, no Cypher, no DAL imports.

---

## Why this is panel-only (Q14b of the migration plan)

Originally `asset_graph.html` shipped with vis-network and inlined the graph data. After migration, the graph viewer is **Neo4j Browser** (richer interaction, queryable). The HTML is reduced to four panels:

1. **Lease-return banner** — colour-coded by `lease_return_state`
2. **Critical Items** — list of `:PriorityItem` from `graph_export.priority_items`
3. **Mandatory Checklist** — 12-item rendering of `:Asset.mandatory_checklist`
4. **Findings (top 25)** — sorted by severity from `graph_export.findings`
5. **Audit Quality** — counts by severity, checklist coverage ratio

The template already implements all of this in vanilla JS — you don't write JavaScript here.

---

## What to log

```
== Phase viz verification ==
- template                : <path to asset_graph_template_panels.html>
- output                  : <workdir>/asset_graph.html
- output size             : <bytes>
- substituted title       : <asset name>
- graph viz proper        : Neo4j Browser at http://localhost:7474
```

---

## MANDATORY VERIFICATION

- `asset_graph.html` exists in workdir.
- File contains the substituted title (open it, grep for the title string).
- File does NOT contain `{{ASSET_TITLE}}` literally (substitution actually happened).

```python
html = (workdir / "asset_graph.html").read_text(encoding="utf-8")
assert "{{ASSET_TITLE}}" not in html, "Title substitution didn't run"
assert title in html, "Title text not present in output"
```

---

## STOP conditions

- Template file not found at `sparengine-export/asset_graph_template_panels.html`.
- `{{ASSET_TITLE}}` still present in the output (substitution failed).
- Output file size < 1 KB (write was incomplete).

---

## What to do if the panels look wrong

The HTML reads `graph_export.json` via `fetch()` at page load. If panels are blank:

1. Open `asset_graph.html` in a browser.
2. Open DevTools → Console; check for fetch errors. The HTML and JSON must be served from the same origin (or both from `file://` and same directory).
3. Confirm `graph_export.json` exists in the same directory and is valid JSON.
4. Re-run Phase 10 if the JSON shape is wrong — the panel JS expects exactly the keys `asset`, `stats`, `priority_items`, `mandatory_checklist`, `findings`, `lease_return_state`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase_viz.py` — verified-working canonical phase viz. For AW139 it produces an 8.8 KB `asset_graph.html` with title "AW139".
