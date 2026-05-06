"""Phase viz — generates the panel-only asset_graph.html.

The new viz pipeline (Q14b) splits responsibilities:
    - Graph exploration → Neo4j Browser at http://localhost:7474
    - Audit panels (Critical Items, Mandatory Checklist, Audit Quality,
      Lease-return banner) → asset_graph.html, which fetches
      graph_export.json and renders four panels.

This phase substitutes ``{{ASSET_TITLE}}`` in the template and writes
asset_graph.html into the workdir. No other transformations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase_viz.py: could not locate graph_dal")


_bootstrap_graph_dal()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument(
        "--template", required=False,
        default="/app/sparengine-export/asset_graph_template_panels.html",
        help="Path to the panel template HTML.",
    )
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    log_path = workdir / "progress.log"

    template_path = Path(args.template).resolve()
    if not template_path.exists():
        # Try a relative fallback (works when running from a checkout).
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            cand = parent / "sparengine-export" / "asset_graph_template_panels.html"
            if cand.exists():
                template_path = cand
                break
    if not template_path.exists():
        raise FileNotFoundError(
            f"phase_viz: template not found at {args.template}. "
            f"Pass --template to point at sparengine-export/asset_graph_template_panels.html"
        )

    # Pick a title from graph_export.json if available
    title = "Sparengine asset"
    export_json = workdir / "graph_export.json"
    if export_json.exists():
        try:
            data = json.loads(export_json.read_text(encoding="utf-8"))
            asset = data.get("asset") or {}
            title = (
                asset.get("name") or asset.get("registration") or asset.get("msn")
                or asset.get("value") or "Sparengine asset"
            )
        except Exception:
            pass

    template = template_path.read_text(encoding="utf-8")
    html = template.replace("{{ASSET_TITLE}}", str(title))

    out_path = workdir / "asset_graph.html"
    out_path.write_text(html, encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n== Phase viz verification ==\n")
        f.write(f"- template                : {template_path}\n")
        f.write(f"- output                  : {out_path}\n")
        f.write(f"- output size             : {out_path.stat().st_size}\n")
        f.write(f"- substituted title       : {title}\n")
        f.write("- graph viz proper        : Neo4j Browser at http://localhost:7474\n")

    print(f"phase_viz: OK — wrote {out_path} ({out_path.stat().st_size} bytes)",
          flush=True)


if __name__ == "__main__":
    main()
