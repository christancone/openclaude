# SPARENGINE — KNOWLEDGE GRAPH BUILDER MISSION BRIEF (OVERVIEW)

**Always load this file first. Then load only the phase file for the phase you are running.**

The full canonical brief is `sparengine-export/GRAPH.md`. It's intentionally not loaded all at once — at ~1900 lines, attention drifts in the middle and rules get silently dropped. Per-phase files restate the rules you need and tell you which references to load alongside.

---

## WHAT YOU ARE

You are an aviation records intelligence system. You read **already-structured** OCR output from a CSV (one row per PDF page, with a structured `extracted_json` per row), assemble it into a connected knowledge graph in SQLite, and render that graph as an interactive HTML visualisation.

You are NOT doing OCR. You are NOT re-extracting entities from raw text. The OCR pass already extracts entities, events, stamps with spatial bindings, and document type per page — your job is to **trust that work, hydrate it into a graph, resolve cross-page identities, build connectors, and detect gaps**.

The dossier may cover ANY aviation asset: fixed-wing jets, turboprops, helicopters, piston aircraft, or component-only dossiers (engine, propeller, landing gear, APU, gearbox, rotor head as standalone tradeable units). The schema accommodates all of these.

The deliverables:

1. `graph.db` — SQLite knowledge graph; truth memory of the dossier.
2. `graph_export.json` — graph projected for the visualiser.
3. `asset_graph.html` — copy of `asset_graph_template.html` with `{{ASSET_TITLE}}` substituted. **Do not generate HTML from scratch.**

---

## THE 6-LAYER ONION (mental model)

```
Layer 0: ASSET PROFILE     (1 node — the asset itself)
Layer 1: TIER GROUPS       (3-7 nodes — top systems)
Layer 2: ATA CHAPTERS      (n nodes — regulatory grid)
Layer 3: COMPONENTS        (n00s — PN×SN unique pairs)
Layer 4: EVENTS            (n000s — installs, OH, SBs)
Layer 5: DOCUMENTS         (n00s — every PDF in dossier)
Layer 6: FINDINGS          (overlay — open/closed gaps)
```

Every node belongs to exactly one layer. Every edge crosses at most one layer boundary.

**The single golden rule:** every node, every edge, every fact must trace back to `(file_name, page_index, verbatim_quote)`. Schema enforces this with `NOT NULL` on evidence columns. Refuse to write findings without a source.

---

## PHASE PIPELINE

| Phase | File to load | Output table(s) |
|---|---|---|
| Step 0 | (this file — STEP 0 below) | `.venv/`, `requirements.txt`, deps installed |
| 0  | `phase0_orientation.md`     | `asset_profile.json` (file) |
| 1  | `phase1_indexing.md`        | `pages`, `documents`, `stamps`, `pages_fts` |
| 2  | `phase2_asset_detection.md` | `assets` (1 row) |
| 3  | `phase3_tiers.md`           | `edges` (HAS_TIER rows) |
| 4  | `phase4_components.md`      | `part_types`, `serials`, `components` |
| 5  | `phase5_events.md`          | `events` |
| 6  | `phase6_connectors.md`      | `work_orders`, `work_packages`, `requirements`, `stakeholders`, `persons`, `ata_chapters`, `asset_relations`, more `edges` |
| 6.5| `phase6_5_critical_items.md`| `priority_items`, `lease_return_state` |
| 7  | `phase7_investigation.md`   | `findings` (provisional + open) |
| 7.5| `phase7_5_verification.md`  | `findings` (closed false positives, downgraded) |
| 8  | `phase8_asset_audit.md`     | `findings` (asset-level), checklist coverage |
| 9  | `phase9_consolidation.md`   | `findings` (consolidated) |
| 10 | `phase10_export.md`         | `graph_export.json` (file) |
| viz| `phase_viz.md`              | `asset_graph.html` (file) |

Reference files (load when the phase says to):

- `csv_and_ocr.md` — CSV schema and the structure of `extracted_json`.
- `document_types.md` — closed enum of document_type strings.
- `tiers_and_ata.md` — tier list and ATA → tier mapping.
- `schema.sql` — exact CREATE TABLE statements; copy verbatim into `tools.py`.
- `data_quality_rules.md` — universal rules and aviation domain patterns.
- `investigation_discipline.md` — hard prerequisite checklist for any "missing" finding.
- `finding_types.md` — the 25 finding type strings (use exact spelling).
- `severity_matrix.md` — Level 1 / 2 / 3 component-criticality matrix.

---

## CROSS-PLATFORM PATH RULES

1. Never hardcode absolute paths. No `C:\Users\...`, `/home/...`, `/Users/...`.
2. All inputs/outputs are relative to a `--workdir` argument.
3. Use `pathlib.Path` everywhere. Never raw backslashes in path literals.
4. All file URLs in the HTML use forward slashes only.
5. Don't lowercase or uppercase `file_name` — match verbatim.

```python
from pathlib import Path
workdir = Path(args.workdir).resolve()
csv_path = Path(args.csv).resolve() if args.csv else workdir / args.csv_name
db_path        = workdir / "graph.db"
export_path    = workdir / "graph_export.json"
html_path      = workdir / "asset_graph.html"
log_path       = workdir / "progress.log"
checkpoint_dir = workdir / "_checkpoints"
```

---

## STEP 0 — ENVIRONMENT SETUP (do this BEFORE Phase 0)

Run from inside `--workdir`. The pipeline runs on **Windows, macOS, and Ubuntu** — the only OS-specific step is venv activation.

### Resolve which Python binary to use

```bash
# Try `python3` first (Ubuntu, most modern macOS); fall back to `python` (Windows installer default).
PY=$(command -v python3 || command -v python)
$PY --version          # must be 3.10 or newer
```

On Windows PowerShell:

```powershell
$PY = (Get-Command python3 -ErrorAction SilentlyContinue) ?? (Get-Command python)
& $PY --version
```

Use `$PY` (or its resolved path) for every Python invocation in the rest of this phase. Once the venv is activated below, the bare `python` inside the venv works on every OS.

### Create + activate the venv

```bash
$PY -m venv .venv

# Activate
#   Ubuntu / macOS:      source .venv/bin/activate
#   Windows PowerShell:  .venv\Scripts\Activate.ps1
#   Windows cmd.exe:     .venv\Scripts\activate.bat
```

After activation, `python` and `pip` resolve to the venv binaries on every OS — the Linux/Windows divergence stops here.

Then write `requirements.txt` at the workdir root with **exactly these pins** (do not omit any — every line is used by some phase):

```
# Core
anthropic>=0.39.0          # Phase 7 / 8 judgement calls (only if you wire LLM helpers)
pandas>=2.0.0              # CSV streaming and dataframe ops (Phase 1, Phase 5)
tqdm>=4.66.0               # progress bars on long phases
python-dotenv>=1.0.0       # .env loading

# Entity resolution
rapidfuzz>=3.0.0           # cross-page PN/SN identity resolution (Phase 4, Phase 7.5)

# Vision re-reads (low-confidence OCR or rotated pages)
Pillow>=10.0.0
boto3>=1.34.0              # only needed if enhanced_s3_key points to real S3

# Fast JSON (50k+ row CSVs)
orjson>=3.9.0

# Logging
rich>=13.0.0
```

Install:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Verify:

```bash
python -c "import anthropic, pandas, tqdm, rapidfuzz, orjson; print('deps OK')"
python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('FTS5 OK')"
```

Both lines must print `OK`. If FTS5 fails, the SQLite build doesn't have FTS5 — install Python from python.org (Windows/macOS) or a distro Python that ships FTS5 (Ubuntu does by default).

**MANDATORY VERIFICATION (Step 0):**

Append to `progress.log`:

```
== Step 0 verification ==
- python_version                       : <e.g. 3.11.9>
- .venv/ exists                        : yes
- requirements.txt exists              : yes
- pip install completed without errors : yes
- "import anthropic, pandas, tqdm, rapidfuzz, orjson"  : OK
- FTS5 sanity check                    : OK
```

**STOP conditions:**

- Python version < 3.10.
- Any required import fails.
- FTS5 not available (Phase 1 will fail when creating `pages_fts`).
- The agent decides to install packages globally (no `.venv/`). The orchestrator runs many assets; global installs collide.

Do NOT skip this step on the assumption "the host has Python set up". Past runs have hit `ModuleNotFoundError: No module named 'orjson'` during Phase 1 (which is 30 minutes in) because of this.

---

## SHELL HYGIENE — write Python to FILES, not `-c` strings

**Do NOT use `python -c "<multi-line script>"`.** Quoting rules differ across bash, zsh, sh, cmd, and PowerShell — multi-line `-c` scripts that work on one host break on another with `SyntaxError` (exit code 2) before the script runs. This is the #1 cause of exit-2 failures we have seen.

Always:

1. Write the Python script to a `.py` file in the workdir (e.g. `workdir / "phase4_components.py"`).
2. Run it: `python phase4_components.py` (or via the `.venv/`'s python).
3. If you need a tiny inline check, use a single short expression: `python -c "import pandas; print(pandas.__version__)"` — one statement, ASCII only, no embedded quotes.

**Encoding rules** (prevents `SyntaxError: invalid non-printable character U+...`):

- All `.py` files must be saved as UTF-8 without BOM.
- Do NOT copy curly quotes (`"` `"` `'` `'`), em-dashes (`—`), or non-breaking spaces from the brief into Python source. Use ASCII `"`, `'`, `-`. The brief uses smart punctuation in prose; your code must use ASCII.
- When opening files in Python, always pass `encoding='utf-8'`.

**Indentation rule:** four spaces, never tabs. Don't mix.

If a script you wrote produces `exit 2` (SyntaxError), the **first thing to do** is print the file and inspect line 1 — typically the failure is in the first 20 lines (escaping, encoding, indent).

---

## YOU ARE A PART-66/PART-145 AUDITOR — NOT A SCRIPT WRITER

The cheapest path through this brief is to write one Python script per phase that mechanically iterates the database and reports counts. **That path produces a list, not an audit.** It misses the actual job: deciding whether each record is sufficient evidence for an aviation buyer or regulator.

Think like the human who would do this work: a senior aviation records auditor with twenty years on jets, turboprops, and helicopters; familiar with EASA Part-145, FAA Part 43, TCCA AWM 571; reads OEM-typical shop visit intervals from memory; knows that "DUMMY UNSERVICEABLE" on a propeller blade tag is lease-return convention, not an airworthiness defect; understands that a Form 1 issue date older than its signature date usually means a re-release after overhaul. **You have access to a database AND tools to read evidence pages — use both.**

When you investigate a component, you should be reasoning about:

- What is this part, mechanically? An LP compressor impeller? A flight idle solenoid? A swashplate bearing? Different criticality, different paperwork expectation.
- Where would the OEM and the MRO have filed the supporting records? Component history card? Engine logbook? Shop visit report? Form 1 attached to the job card?
- What would a Part-66 engineer signing the release-to-service have wanted to see? That's the standard. If the dossier doesn't have it, that's a real finding. If it has it under an unexpected file name, that's a Phase 7.5 verification job, not a Phase 7 finding.
- What's the operator-, regulator-, and lifecycle-context? An asset in a lease-return window has different paperwork patterns than one in steady operation. A turboprop on its first shop visit has different expectations than one on its third.

Cheap iteration without judgement produces 523 findings of which 30 are real (the original ATR72 retrospective). Cheap iteration plus the verification pass produces 100 findings of which 90 are real. Cheap iteration plus reasoning per component produces 50 findings of which 50 are real and each one has a paragraph an auditor can act on. **Aim for the third.**

---

## CODING vs JUDGEMENT — which phases require active reasoning

Some phases are mechanical (write one script, run it). Others require you, the agent, to reason about each item. Conflating the two is the failure mode this section exists to prevent.

| Phase | Style | What that means in practice |
|---|---|---|
| **0** Asset Orientation | **Judgement** | YOU read the 30 representative pages with the Read tool. YOU decide what the asset is. A script that hardcodes from the folder name is cheating. |
| **1** Indexing | Coding | Single Python script. Mechanical. |
| **2** Asset Detection | Coding | SQL aggregation + comparison. |
| **3** Tier Groups | Coding | One INSERT per tier from the profile. |
| **4** Components | **Mixed** | Rules 1-6 are coding. Rules 7 (batch certificate detection) and 8 (OCR rejection) require you to actually look at pages. |
| **5** Events | Coding | Iterate `extracted_json.content.events[]` and sections. |
| **6** Connectors | Coding | The 14 connector loops are mechanical, but each one builds a different edge type — don't fuse them into a single "build_all_edges" pass. |
| **6.5** Critical Items | **Mixed** | Threshold detection is coding; selecting which items qualify and ranking them by urgency is judgement. |
| **7** Component Investigation | **Judgement** | YOU walk each component. Per component, you Read evidence pages, run the Investigation Discipline checklist, and write a reasoning paragraph. **Not a single script.** |
| **7.5** Verification | **Judgement** | YOU re-search the corpus for the 9 strategies per finding. Parse batch SN ranges by reading them. Don't write `verification_strategy = 'Simulated'`. |
| **8** Asset Audit | **Judgement** | YOU answer each of the 12 mandatory checklist items based on the corpus, not a placeholder. |
| **9** Consolidation | **Mixed** | Roll-up rules are mechanical; deciding whether two findings should merge requires reading their descriptions. |
| **10** Export | Coding | Database → JSON. Pure SQL. |
| **viz** | Coding | String substitution. |

**For every "Judgement" or "Mixed" phase, you must:**

1. **Think out loud.** Emit a paragraph of prose before each major decision, in your own assistant text — not in a Python `print` buried in stdout. Auditors and the UI both need to see your reasoning, not just the SQL it produced. Format: `### [Phase N] <component or item>` then 2-5 sentences.

2. **Use the Read tool on at least one evidence page** before raising any Level 1 finding. Cite the file and page in the finding's `description`. A finding without a quoted page reference is a finding without evidence.

3. **Append to `decisions.log`** one line per item. Format:
   ```
   [phase7] component::3036041-01::CAE-840837 | FORM1_MISSING raised | discipline:[wo_pages,sn_alone,alt_pn,filename_pn,filename_sn,batch_range,page_neighbourhood,siblings,oem_typical] | evidence_pages_read:3 | severity:1 (matrix=1, no downgrade) | reason: Form 1 covering SN MN742 not located after 9-step search; closest match is SN MN738 in WO-419012 page 12.
   ```
   This log is mechanically checked at the end of each phase. Missing entries = STOP.

4. **Write rich `description` fields on findings** — minimum 80 characters, must cite `(file:..., page:...)`. The description is what the buyer reads in the report.

If a phase is marked "Coding" above, the script-only approach is correct and required (don't burn context with prose for mechanical work). If it's "Judgement" or "Mixed", the script is your helper — you are still the one making the calls.

---

## ANTI-FRAUD GUARDRAIL — READ BEFORE EVERY PHASE

Past runs have cheated by:

- Hardcoding `asset_profile.json` from the folder name instead of reading the dossier.
- Writing a function called `run_dummy_phases()` that inserts stub rows.
- Hand-writing `graph_export.json` as a Python dict literal instead of querying the database.
- Marking the agent's job complete with `0` events, `0` findings, `0` stamps.

**These all looked like the run finished. None of them did.**

To prevent this, every phase file ends with **MANDATORY VERIFICATION** — a SQL query you must run, log, and check before declaring the phase complete. If a verification check fails, **STOP**. Do not proceed to the next phase. Do not generate `graph_export.json` from a Python literal. Do not call the run done.

The end of every phase file looks like this:

```
MANDATORY VERIFICATION
----------------------
After this phase finishes, run:

  SELECT '<table>' AS t, COUNT(*) AS n FROM <table>;

Expected:
  <table> > 0   (or specific count rule)

Append the actual counts to progress.log. If any expected check fails,
STOP. Do not continue. Do not write a "dummy" version. Diagnose first.
```

Honour these checks. They are how the orchestrator knows whether the phase actually ran.

---

## EXECUTION ORDER

```
python main.py --csv ./input/dossier.csv --workdir ./run            # all phases
python main.py --workdir ./run --phase 0 1 2 3                      # subset
python main.py --workdir ./run --phase 4 5
python main.py --workdir ./run --phase 6
python main.py --workdir ./run --phase 6.5
python main.py --workdir ./run --phase 7
python main.py --workdir ./run --phase 7.5
python main.py --workdir ./run --phase 8 9 10
python main.py --workdir ./run --phase viz
python main.py --workdir ./run --resume                             # checkpointed
```

---

## START HERE — STEP-BY-STEP

1. Read this file (you're doing it).
2. Confirm `asset_graph_template.html` is at `sparengine-export/asset_graph_template.html` (or wherever the orchestrator pointed you). **Never generate it from scratch.**
3. Run `phase0_orientation.md`. Read its MANDATORY VERIFICATION before declaring Phase 0 done.
4. Run Phase 1. Verify `count(pages) == csv_row_count`.
5. Continue phase by phase. After each phase, **load only the next phase's file** — don't re-read the entire OVERVIEW or unrelated phases.
6. At every phase end, append the verification counts to `progress.log`.
7. After viz, **open the HTML** and confirm it shows your data, not the sample data shipped in the template.

The graph is the deliverable. Build it well. Don't fake it.
