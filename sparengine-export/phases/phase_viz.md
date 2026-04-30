# PHASE viz — Generate `asset_graph.html`

**Intent.** Copy `asset_graph_template.html`, substitute the asset title, write `asset_graph.html` next to `graph_export.json`. **String substitution only.**

**Inputs:** `assets` row, `graph_export.json`, `asset_graph_template.html`.

---

## ANTI-CHEAT RULE

`viz.py` does **string substitution and nothing else**.

- Do NOT generate any HTML, CSS, or JavaScript inline in Python.
- Do NOT write an `<html>` string literal.
- Do NOT modify the template's vis-network options.
- Do NOT change the colour palette, font, layout, or class names.
- Do NOT "simplify" the template by stripping unused features.

Past runs have produced a 60 KB hand-built HTML that loaded a default vis-network white-background graph with no toolbar, no panel, no legend. **That is the failure mode this rule exists to prevent.** If `viz.py` is more than ~50 lines, it's probably doing the wrong thing.

---

## Steps

```python
from pathlib import Path
import argparse, json, sqlite3

def run():
    p = argparse.ArgumentParser()
    p.add_argument('--workdir',  default='.')
    p.add_argument('--template', default=None,
                   help='Path to asset_graph_template.html (default: workdir/asset_graph_template.html)')
    args = p.parse_args()

    workdir       = Path(args.workdir).resolve()
    template_path = Path(args.template) if args.template else workdir / 'asset_graph_template.html'
    export_path   = workdir / 'graph_export.json'
    db_path       = workdir / 'graph.db'
    out_path      = workdir / 'asset_graph.html'

    # 1. Verify inputs.
    assert template_path.exists(), f"Template missing: {template_path}"
    assert export_path.exists(),   f"graph_export.json missing — run Phase 10 first"
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

    print(f"Wrote {out_path} (title: {asset_title!r})")

if __name__ == '__main__':
    run()
```

That's the entire `viz.py`. ~40 lines. If yours is longer, you're doing it wrong.

---

## After viz: optional inline-data fallback

The template loads `graph_export.json` via `fetch('graph_export.json')`. Some browsers (Chrome) block `fetch()` over `file://` due to CORS. If that's a concern for the user, the orchestrator's post-run inliner (`sparengine-export/inline_graph.mjs`) will swap the fetch for an inlined `<script type="application/json">` block. **`viz.py` itself does not need to do this** — the inliner handles it.

---

## MANDATORY VERIFICATION

```bash
ls -la asset_graph.html
```

Append to `progress.log`:

```
- asset_graph.html exists                : yes
- asset_graph.html size                  : ~ template_size + few bytes
- title substituted                      : yes (no '{{ASSET_TITLE}}' literal in output)
- bytes diff (template vs output)        : ~ few hundred (just the title length)
```

**STOP conditions:**

- `asset_graph.html` doesn't exist.
- `asset_graph.html` is significantly smaller than the template — means the template was overwritten with hand-generated HTML.
- `asset_graph.html` contains the literal `{{ASSET_TITLE}}` (substitution didn't happen).
- `asset_graph.html` size < 50 KB — means it's a stub, not a copy of the template.
