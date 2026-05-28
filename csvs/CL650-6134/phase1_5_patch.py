"""Phase 1.5 — Backfill Form1 block properties from header_fields.

The initial Phase 1 ran before header_fields support landed in
_extract_form1_fields. This patch re-streams the CSV and updates any
:Form1 node whose properties (block_11_status, block_4_issuer_text,
block_13_date, block_13c_cert_number, block_13b_name, etc.) are NULL
but whose carrier page's extracted_json has them in header_fields.

It does NOT create new Form 1 nodes or new edges. It only `SET` properties
on existing nodes via `coalesce(...)` so existing values are preserved.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import orjson
import pandas as pd


def _bootstrap() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return


_bootstrap()
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name
from graph_dal._normalize import normalize_date
from graph_dal._phase_tag import phase


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
CSV_PATH = Path("D:/work/openclaude/csvs/Full challenger dossier.csv")
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"


def _log(line: str) -> None:
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _hf_get(hf: dict, *patterns: str) -> str | None:
    for pat in patterns:
        for k, v in hf.items():
            if pat in k.lower() and isinstance(v, str) and v.strip():
                return v.strip()
    return None


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 1.5 START {start.isoformat()} ==========")
    driver = connect()

    # First, pull the set of Form1-carrier page UIDs from the graph.
    with driver.session(database=database_name()) as s:
        f1_pages = {r["puid"]: r["fuid"] for r in s.run("""
            MATCH (p:Page {asset_id:$aid})-[:CARRIES]->(f:Form1)
            RETURN p.value AS puid, f.value AS fuid
        """, aid=ASSET_ID)}
    _log(f"[phase1.5] {len(f1_pages)} form1-carrier pages")

    updated = 0
    with phase("phase1_5_patch"):
        with driver.session(database=database_name()) as session:
            BATCH = 200
            buffer = []
            for chunk in pd.read_csv(CSV_PATH, chunksize=500):
                # Filter to rows whose id is a Form1 page.
                rows = chunk[chunk["id"].isin(f1_pages.keys())]
                if rows.empty:
                    continue
                with session.begin_transaction() as tx:
                    for _, row in rows.iterrows():
                        page_uid = str(row["id"])
                        f_uid = f1_pages[page_uid]
                        try:
                            ext = orjson.loads(row["extracted_json"])
                        except Exception:
                            continue
                        hf = ext.get("header_fields") if isinstance(ext.get("header_fields"), dict) else {}
                        if not hf:
                            continue
                        status = _hf_get(hf, "11. status", "11.status")
                        issuer = _hf_get(hf, "4. organization", "4. approved organization",
                                         "4. approving organization", "4.organization")
                        cert = _hf_get(hf, "14c. approval", "14c.approval", "14c. certificate",
                                       "13c. approval", "13c.approval", "13c. certificate")
                        name = _hf_get(hf, "14d. name", "14d.name", "13d. name", "13d.name")
                        date_raw = _hf_get(hf, "14e. date", "14e.date", "13e. date", "13e.date")
                        date_iso = normalize_date(date_raw) or date_raw if date_raw else None
                        mod_status = _hf_get(hf, "12. mod", "12.mod", "modification status")
                        batch_no = _hf_get(hf, "7. batch", "7.batch", "7. lot")
                        tx.run("""
                            MATCH (f:Form1 {asset_id:$aid, value:$fuid})
                            SET f.block_11_status = coalesce(f.block_11_status, $status),
                                f.block_4_issuer_text = coalesce(f.block_4_issuer_text, $issuer),
                                f.block_13c_cert_number = coalesce(f.block_13c_cert_number, $cert),
                                f.block_13b_name = coalesce(f.block_13b_name, $name),
                                f.block_13_date = coalesce(f.block_13_date, $date),
                                f.block_12_mod_status = coalesce(f.block_12_mod_status, $mod_status),
                                f.block_7_batch = coalesce(f.block_7_batch, $batch_no)
                        """, aid=ASSET_ID, fuid=f_uid,
                            status=status, issuer=issuer, cert=cert, name=name,
                            date=date_iso, mod_status=mod_status, batch_no=batch_no).consume()
                        updated += 1
                    tx.commit()
    _log(f"[phase1.5] updated {updated} form1 nodes")
    driver.close()
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 1.5 END {end.isoformat()} dur={int((end-start).total_seconds())}s ==========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
