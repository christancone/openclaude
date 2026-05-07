// Tiny dependency-free server: serves a frontend, accepts asset IDs,
// pages through the export_asset_pages RPC on Supabase, and writes a CSV
// to the repo root. Streams progress to the browser via SSE.
//
// Run:   node sparengine-export/server.mjs
// Open:  http://localhost:2001

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot  = path.resolve(__dirname, '..');

// --- minimal .env loader (root .env) -----------------------------------------
const envPath = path.join(repoRoot, '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
}

const SUPABASE_URL = process.env.SUPABASE_URL;
const SERVICE_KEY  = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!SUPABASE_URL || !SERVICE_KEY) {
  console.error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env');
  process.exit(1);
}

const PORT      = Number(process.env.PORT || 2001);
// Supabase's hosted PostgREST enforces db-max-rows (default 1000) and ignores
// the Range header. Keep page size <= that cap; the loop pages until empty.
const PAGE_SIZE = Number(process.env.EXPORT_PAGE_SIZE || 1000);

// --- CSV helpers -------------------------------------------------------------
const COLUMNS = [
  'id', 'document_id', 'page_index', 'original_path', 'rotation_deg',
  'is_blank', 'is_template_empty', 'is_removed', 'extracted_json',
  'enhanced_s3_key', 'created_at', 'file_name', 'file_type', 'asset_id',
  'chunk_count', 'chunks_with_embeddings', 'chunks',
];

function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = (typeof v === 'object') ? JSON.stringify(v) : String(v);
  // RFC 4180: quote if contains ", , \r, or \n
  if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}
function csvRow(row) {
  return COLUMNS.map(c => csvCell(row[c])).join(',') + '\n';
}

// --- Supabase REST: fetch asset names ----------------------------------------
async function fetchAssetNames(assetIds) {
  const inList = assetIds.map(id => `"${id}"`).join(',');
  const url    = `${SUPABASE_URL}/rest/v1/assets?select=id,name&id=in.(${inList})`;
  const res = await fetch(url, {
    headers: {
      'apikey':        SERVICE_KEY,
      'Authorization': `Bearer ${SERVICE_KEY}`,
      'Accept':        'application/json',
    },
  });
  if (!res.ok) {
    throw new Error(`Asset name lookup failed (${res.status}): ${await res.text()}`);
  }
  const rows = await res.json();
  const map  = new Map();
  for (const r of rows) map.set(r.id, r.name);
  return map;
}

function sanitize(s) {
  return String(s || '')
    .replace(/\.[^.]+$/, '')        // drop trailing extension (e.g. .zip)
    .replace(/[\\/:*?"<>|]+/g, '_') // illegal on Windows
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'unnamed';
}

// --- Supabase RPC call -------------------------------------------------------
async function callRpc(assetIds, limit, offset) {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/export_asset_pages`, {
    method: 'POST',
    headers: {
      'Content-Type':  'application/json',
      'apikey':        SERVICE_KEY,
      'Authorization': `Bearer ${SERVICE_KEY}`,
      'Accept':        'application/json',
      // Bypass PostgREST's default 1000-row response cap. The RPC's own
      // p_limit still controls the page size; this just lets the response
      // carry that many rows.
      'Range-Unit':    'items',
      'Range':         `0-${limit - 1}`,
    },
    body: JSON.stringify({
      p_asset_ids: assetIds,
      p_limit:     limit,
      p_offset:    offset,
    }),
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`Supabase RPC failed (${res.status}): ${txt}`);
  }
  return res.json();
}

// --- frontend ----------------------------------------------------------------
const indexHtml = fs.readFileSync(path.join(__dirname, 'public', 'index.html'), 'utf8');

// --- multipart parser (no external deps) -------------------------------------
//
// We only support the common case: small-medium CSV uploads + a few text
// fields. Buffers the whole body in memory; size-bounded by MAX_UPLOAD_BYTES.
// For 50 MB CSVs that's well within node's heap defaults.
//
// Returns: [{ name, filename?, content (Buffer), contentType? }, ...]

const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;   // 200 MB — generous for big OCR'd dossiers

async function parseMultipart(req) {
  const ct = req.headers['content-type'] || '';
  const m  = /boundary=([^;\s]+)/.exec(ct);
  if (!m) throw new Error('no multipart boundary in Content-Type');
  const boundary = Buffer.from('--' + m[1]);

  // Read the full body with a hard size cap.
  let total = 0;
  const chunks = [];
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_UPLOAD_BYTES) {
      throw new Error(`upload exceeds ${MAX_UPLOAD_BYTES / (1024*1024)} MB cap`);
    }
    chunks.push(chunk);
  }
  const body = Buffer.concat(chunks);

  const parts = [];
  let pos = 0;
  while (pos < body.length) {
    // Find next boundary.
    const bIdx = body.indexOf(boundary, pos);
    if (bIdx < 0) break;
    pos = bIdx + boundary.length;

    // `--` after boundary marks end-of-stream.
    if (body.slice(pos, pos + 2).toString() === '--') break;

    // CRLF after boundary.
    if (body.slice(pos, pos + 2).toString() === '\r\n') pos += 2;

    // Headers end at \r\n\r\n.
    const headerEnd = body.indexOf(Buffer.from('\r\n\r\n'), pos);
    if (headerEnd < 0) break;
    const headerStr = body.slice(pos, headerEnd).toString('utf8');
    const contentStart = headerEnd + 4;

    // Content ends at the next \r\n + boundary.
    const nextBoundary = body.indexOf(boundary, contentStart);
    if (nextBoundary < 0) break;
    // Strip trailing \r\n that precedes the boundary.
    const contentEnd = nextBoundary - 2;

    const nameMatch     = /name="([^"]+)"/i.exec(headerStr);
    const filenameMatch = /filename="([^"]*)"/i.exec(headerStr);
    const ctMatch       = /content-type:\s*([^\r\n;]+)/i.exec(headerStr);

    parts.push({
      name:        nameMatch     ? nameMatch[1]     : null,
      filename:    filenameMatch ? filenameMatch[1] : null,
      contentType: ctMatch       ? ctMatch[1].trim(): null,
      content:     body.slice(contentStart, contentEnd),
    });
    pos = nextBoundary;
  }
  return parts;
}

function readPart(parts, name) {
  const p = parts.find(p => p.name === name);
  return p ? p.content.toString('utf8') : null;
}
function readFilePart(parts, name) {
  const p = parts.find(p => p.name === name && p.filename);
  return p || null;
}

// --- export driver -----------------------------------------------------------
async function runExport(assetIds, send, opts = {}) {
  // Resolve asset names so we can build a friendly folder name.
  let nameMap;
  try {
    nameMap = await fetchAssetNames(assetIds);
  } catch (e) {
    send({ type: 'error', message: e.message });
    return;
  }
  const missing = assetIds.filter(id => !nameMap.has(id));
  if (missing.length) {
    send({ type: 'warn', message: `Asset(s) not found: ${missing.join(', ')}` });
  }

  const first      = assetIds[0];
  const firstName  = sanitize(nameMap.get(first) || first);
  const folderName = assetIds.length === 1
    ? `${first}-${firstName}`
    : `${first}-${firstName}+${assetIds.length - 1}more`;

  const folderPath = path.join(repoRoot, 'csvs', folderName);
  fs.mkdirSync(folderPath, { recursive: true });

  const ts       = new Date().toISOString().replace(/[:.]/g, '-');
  const fileName = `asset_pages_${ts}.csv`;
  const filePath = path.join(folderPath, fileName);
  const relPath  = path.relative(repoRoot, filePath).replace(/\\/g, '/');
  const out      = fs.createWriteStream(filePath, { encoding: 'utf8' });

  out.write(COLUMNS.join(',') + '\n');

  let total  = 0;
  let offset = 0;

  send({ type: 'start', assetIds, file: relPath });

  while (true) {
    let rows;
    try {
      rows = await callRpc(assetIds, PAGE_SIZE, offset);
    } catch (e) {
      send({ type: 'error', message: e.message });
      out.end();
      return;
    }
    if (!Array.isArray(rows) || rows.length === 0) break;

    for (const row of rows) out.write(csvRow(row));
    total  += rows.length;
    offset += rows.length;

    send({ type: 'progress', rows: total });
    // Don't trust rows.length < PAGE_SIZE as "done" — Supabase may truncate
    // each response below PAGE_SIZE. Only stop on an empty batch.
  }

  await new Promise(r => out.end(r));
  send({ type: 'done', rows: total, file: relPath, path: filePath });

  if (opts.runAgent) {
    await runPostCsvPipeline({
      folderPath, folderName, fileName,
      assetId: first,                        // UUID matches :Asset.asset_id
      send,
    });
  }
}

// Shared post-CSV pipeline: agent run → mechanical scorecard → LLM-as-judge
// → graph-ready redirect to Neo4j Browser. Used by both /export (Supabase
// pull) and /upload-csv (direct upload). Best-effort QA layers — failures
// are surfaced as agent-log entries but don't stop the run.
async function runPostCsvPipeline({ folderPath, folderName, fileName, assetId, send }) {
  await runAgent(folderPath, fileName, send);

  try {
    await runScorecard(folderPath, send);
  } catch (e) {
    send({ type: 'agent-log', stream: 'stderr',
           line: `quality_scorecard failed: ${e.message}` });
  }
  try {
    await runLlmJudge(folderPath, send);
  } catch (e) {
    send({ type: 'agent-log', stream: 'stderr',
           line: `llm_judge failed: ${e.message}` });
  }

  // Hand the user a Neo4j Browser link pre-filled with a Cypher query
  // scoped to this asset_id. The Browser is the canonical UI.
  const folderUrl  = `/csvs/${encodeURIComponent(folderName)}/`;
  const cypher     = `MATCH (n {asset_id: '${assetId.replace(/'/g, "\\'")}'}) RETURN n LIMIT 300`;
  const browserUrl = neo4jBrowserUrl(cypher);
  send({
    type: 'graph-ready',
    asset_id: assetId,
    folderUrl,
    neo4jBrowserUrl: browserUrl,
    jsonUrl: folderUrl + 'graph_export.json',
    cypher,
  });
}

// Drive a run from a CSV the user uploaded directly (bypassing Supabase).
//
//   1. Sniff the CSV's row 0 for an `asset_id` column. If present, that's
//      the asset UUID; if absent, we generate a `upload-<uuid4>` so the
//      Neo4j multi-tenancy invariant still holds.
//   2. Build a workdir at `/app/csvs/<asset_id>-<label>/` and save the CSV
//      there as `<original_filename>` (sanitised).
//   3. Stream `start` / `done` SSE events for the upload phase, then run
//      the same post-CSV pipeline as /export.
async function runFromUpload({ csvBuffer, originalFilename, label, runAgentFlag, send }) {
  // 1. Sniff asset_id from row 0 (best-effort; fall back to UUID).
  let assetId = sniffAssetId(csvBuffer);
  if (!assetId) {
    assetId = 'upload-' + cryptoUuid();
    send({ type: 'warn',
           message: `No asset_id column in CSV — generated ${assetId}.` });
  }
  const safeLabel  = sanitize(label || stripExt(originalFilename) || 'upload');
  const folderName = `${assetId}-${safeLabel}`;
  const folderPath = path.join(repoRoot, 'csvs', folderName);
  fs.mkdirSync(folderPath, { recursive: true });

  // 2. Save the CSV. `sanitize()` drops the extension (it's tuned for folder
  // names), so we sanitise the stem and re-attach `.csv` ourselves.
  const stem    = sanitize(stripExt(originalFilename) || 'dossier');
  const fileName = stem + '.csv';
  const filePath = path.join(folderPath, fileName);
  const relPath  = path.relative(repoRoot, filePath).replace(/\\/g, '/');
  fs.writeFileSync(filePath, csvBuffer);

  send({ type: 'start',    file: relPath, assetIds: [assetId] });
  send({ type: 'progress', rows: countCsvRows(csvBuffer) });
  send({ type: 'done',     rows: countCsvRows(csvBuffer), file: relPath, path: filePath });

  // 3. Post-CSV pipeline (agent + QA + redirect).
  if (runAgentFlag) {
    await runPostCsvPipeline({ folderPath, folderName, fileName, assetId, send });
  }
}

// --- upload helpers ----------------------------------------------------------

function cryptoUuid() {
  // node ≥ 19 has crypto.randomUUID at top-level via globalThis
  return (globalThis.crypto && globalThis.crypto.randomUUID)
    ? globalThis.crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function stripExt(filename) {
  if (!filename) return '';
  const i = filename.lastIndexOf('.');
  return i > 0 ? filename.slice(0, i) : filename;
}

function sniffAssetId(buffer) {
  // Read the first ~16 KB — enough for header + first data row even on
  // very wide CSVs.
  const slice = buffer.slice(0, Math.min(buffer.length, 16384)).toString('utf8');
  const lines = slice.split(/\r?\n/);
  if (lines.length < 2) return null;
  const headers = parseCsvHeader(lines[0]);
  const idx = headers.findIndex(h => h.trim().toLowerCase() === 'asset_id');
  if (idx < 0) return null;
  const cells = parseCsvHeader(lines[1]);
  const value = (cells[idx] || '').trim();
  return value || null;
}

// Minimal CSV header parser: handles quoted cells with embedded commas
// and "" escaping. Used only for asset_id sniffing — the production CSV
// has well-defined columns; this just needs to be robust enough for the
// first two rows.
function parseCsvHeader(line) {
  const out = [];
  let cur = '', inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQ) {
      if (c === '"' && line[i+1] === '"') { cur += '"'; i++; continue; }
      if (c === '"') { inQ = false; continue; }
      cur += c;
    } else {
      if (c === '"' && cur === '') { inQ = true; continue; }
      if (c === ',') { out.push(cur); cur = ''; continue; }
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

function countCsvRows(buffer) {
  // Approximate row count = newline count - 1 (header). Cheap heuristic
  // for the SSE progress event; the agent reads the CSV authoritatively.
  let n = 0;
  for (let i = 0; i < buffer.length; i++) if (buffer[i] === 0x0a) n++;
  return Math.max(0, n - 1);
}

// Layer B LLM-as-judge sampling. Reuses the production AGENT_CLI to score
// 10 stratified findings on a 1..5 scale; writes llm_judgement.json into
// the workdir and merges llm_mean / llm_p20 into the existing
// :QualityScorecard. Set SKIP_LLM_JUDGE=1 in the env to disable for runs
// where you've exhausted your agent budget — the tool is also runnable
// via `make llm-judge-skip ASSET=...` to write null scores explicitly.
async function runLlmJudge(folderPath, send) {
  if (process.env.SKIP_LLM_JUDGE === '1') {
    send({ type: 'agent-log', stream: 'stderr',
           line: '[llm_judge] skipped (SKIP_LLM_JUDGE=1)' });
    return;
  }
  const { spawn } = await import('node:child_process');
  return new Promise((resolve) => {
    const args = ['-m', 'tools.llm_judge', '--workdir', folderPath, '--sample', '10'];
    if (process.env.LLM_JUDGE_NO_LLM === '1') args.push('--no-llm');
    const proc = spawn(
      'python', args,
      { cwd: __dirname, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] },
    );
    let stderr = '';
    proc.stderr.on('data', d => {
      const text = d.toString().trim();
      stderr += text + '\n';
      send({ type: 'agent-log', stream: 'stderr', line: `[llm_judge] ${text}` });
    });
    proc.on('close', (code) => {
      if (code !== 0) {
        send({ type: 'agent-log', stream: 'stderr',
               line: `llm_judge exited ${code}` });
        resolve(null);
        return;
      }
      // The tool's stderr summary line looks like:
      //   "llm_mean=4.2 sample=10 cost_usd=$0.0500"
      // Parse it for the SSE event payload.
      const meanMatch  = stderr.match(/llm_mean=([\d.]+|None|null)/);
      const sampleMatch = stderr.match(/sample=(\d+)/);
      const costMatch  = stderr.match(/cost_usd=\$?([\d.]+)/);
      send({
        type: 'llm-judgement',
        llm_mean: meanMatch ? (meanMatch[1] === 'None' ? null : parseFloat(meanMatch[1])) : null,
        llm_sample_size: sampleMatch ? parseInt(sampleMatch[1], 10) : 0,
        llm_total_cost_usd: costMatch ? parseFloat(costMatch[1]) : 0.0,
      });
      resolve(null);
    });
  });
}

// Run the Layer B mechanical scorecard against a finished workdir.
// Spawned as a subprocess so a Python crash doesn't take the orchestrator
// with it. Streams summary line back via the `quality-scorecard` SSE event.
async function runScorecard(folderPath, send) {
  const { spawn } = await import('node:child_process');
  return new Promise((resolve) => {
    const proc = spawn(
      'python', ['-m', 'tools.quality_scorecard', '--workdir', folderPath, '--print'],
      { cwd: __dirname, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] },
    );
    let out = '';
    proc.stdout.on('data', d => { out += d.toString(); });
    proc.stderr.on('data', d => {
      send({ type: 'agent-log', stream: 'stderr',
             line: `[scorecard] ${d.toString().trim()}` });
    });
    proc.on('close', (code) => {
      if (code !== 0) {
        send({ type: 'agent-log', stream: 'stderr',
               line: `quality_scorecard exited ${code}` });
        resolve(null);
        return;
      }
      try {
        const scorecard = JSON.parse(out);
        send({
          type: 'quality-scorecard',
          mechanical_overall: scorecard.mechanical_overall,
          fact_orphan_count:  scorecard.fact_orphan_count,
          total_findings:     scorecard.total_findings,
          level_1_count:      scorecard.level_1_count,
          asset_id:           scorecard.asset_id,
          run_id:             scorecard.run_id,
        });
      } catch (e) {
        send({ type: 'agent-log', stream: 'stderr',
               line: `scorecard JSON parse failed: ${e.message}` });
      }
      resolve(null);
    });
  });
}

// Build a Neo4j Browser URL that pre-fills the editor with a Cypher query.
// `cmd=edit&arg=<encoded>` puts the query in the editor; user runs it with
// Ctrl+Enter. The host/port come from NEO4J_BROWSER_URL (or default to the
// standard 7474). We use http for localhost — the Browser auto-detects the
// running Bolt connection from cookies.
function neo4jBrowserUrl(cypher) {
  const base = process.env.NEO4J_BROWSER_URL || 'http://localhost:7474';
  return `${base}/browser/?cmd=edit&arg=${encodeURIComponent(cypher)}`;
}

// --- Agent run --------------------------------------------------------------
// Spawn OpenClaude / Claude Code in headless --print mode against the asset
// folder, pointing at sparengine-export/phases/briefs/OVERVIEW.md (the Neo4j-era
// brief) as the instruction set. Streams stdout/stderr to the SSE client.

const OVERVIEW_MD    = path.join(__dirname, 'phases', 'briefs', 'OVERVIEW.md');
const PHASES_DIR     = path.join(__dirname, 'phases');
const BRIEFS_DIR     = path.join(__dirname, 'phases', 'briefs');
const REFERENCES_DIR = path.join(__dirname, 'phases', 'references');
const CYPHER_DIR     = path.join(__dirname, 'phases', 'cypher');
const CLI_MJS        = path.join(repoRoot, 'dist', 'cli.mjs');

// AGENT_CLI controls which CLI runs the graph-builder agent.
//   "claude"     -> official Claude Code CLI (uses your Pro/Max subscription)
//   "openclaude" -> local OpenClaude build at dist/cli.mjs
const AGENT_CLI = (process.env.AGENT_CLI || 'openclaude').toLowerCase();

// One concurrent agent at a time. Stored so /agent/stop can kill it.
let activeAgent = null;

function buildAgentPrompt(csvFileName) {
  const phases  = PHASES_DIR.replace(/\\/g, '/');
  const briefs  = BRIEFS_DIR.replace(/\\/g, '/');
  const refs    = REFERENCES_DIR.replace(/\\/g, '/');
  const cypher  = CYPHER_DIR.replace(/\\/g, '/');
  return [
    'You are a Part-66/Part-145 aviation records auditor with twenty years of fleet',
    'experience. You are NOT a script writer. The dossier in this working directory',
    'is one aviation asset (aircraft, engine, propeller, gearbox, or component-only).',
    'Your job is to build an audit-grade knowledge graph in **Neo4j** and tell the',
    'buyer what is documented, what is missing, and what is at risk — with evidence',
    'cited per finding.',
    '',
    'STORAGE MODEL — there is no SQLite. There is no graph.db file. There is no FTS5.',
    'The graph lives in a shared Neo4j Community instance reachable at the URI in your',
    '.env (typically bolt://neo4j:7687). All writes go through the `graph_dal/` Python',
    'package — phase scripts MUST NOT construct raw Cypher for writes. Phase 3 is',
    '**deleted** (Tier as a graph layer was killed); the pipeline goes 0 → 1 → 2 → 4.',
    '',
    'You have Bash, Read, Write, Edit, and Grep tools. Use them like an auditor:',
    '  - Bash for Cypher probes (cypher-shell or via neo4j Python driver) and Python',
    '    scripts that drive `graph_dal` writers.',
    '  - Read to OPEN evidence pages cited by `:Page.original_path` — you must look',
    '    at the page, not just trust the index. A finding without an opened page is',
    '    incomplete. The DAL helper `graph_dal.cite.cite_node()` turns any node into',
    '    `(file_name, page_index, original_path)` for citation.',
    '  - Write a paragraph of reasoning before each major decision; the UI streams',
    '    your assistant text to the user. Do not silently emit tool calls only —',
    '    think out loud, in 3-5 sentences per component, citing what you expected',
    '    and what you found.',
    '',
    `The OCR CSV is at ./${csvFileName} (relative to your CWD).`,
    '',
    `Your mission brief is split into focused files under: ${phases}/`,
    `  ${briefs}/   — phase briefs (workflow, one file per phase)`,
    `  ${refs}/     — reference docs (rules consumed by briefs)`,
    `  ${cypher}/   — Cypher schema + caption files (applied automatically by neo4j-init)`,
    '',
    'STEP 1 — Always read these two files first:',
    `  ${briefs}/OVERVIEW.md             — pipeline + golden rules + STEP 0 environment setup`,
    `  ${refs}/csv_and_ocr.md            — CSV schema and the structure of extracted_json`,
    '',
    'STEP 2 — Run STEP 0 from OVERVIEW.md: create a .venv, write requirements.txt',
    '  (which MUST include `neo4j>=5.20.0`), pip install, verify the `neo4j` import',
    '  works AND verify the schema is in place (call `graph_dal.verify.verify_schema`).',
    '  DO NOT skip this — Phase 1 will fail at the first DAL write otherwise.',
    '',
    'STEP 3 — Then load ONE phase brief at a time, in order, and execute that phase:',
    `  ${briefs}/phase0_orientation.md       Phase 0  (asset profile)`,
    `  ${briefs}/phase1_indexing.md          Phase 1  (CSV → pages/documents/stamps/evidence records/identifiers/dates)`,
    `  ${briefs}/phase2_asset_detection.md   Phase 2  (:Asset confirmation + secondary class label + reconciliation)`,
    `  ${briefs}/phase4_components.md        Phase 4  (:Component hydration — Phase 3 is deleted, do not look for it)`,
    `  ${briefs}/phase5_events.md            Phase 5  (:Event + :ComponentSnapshot)`,
    `  ${briefs}/phase6_connectors.md        Phase 6  (:Person/:Organization + cross-doc :INCLUDES, :SIGNED_BY, :STAMPED_BY)`,
    `  ${briefs}/phase6_5_critical_items.md  Phase 6.5 (:PriorityItem + lease_return_state)`,
    `  ${briefs}/phase7_investigation.md     Phase 7  (per-component :Finding via DAL)`,
    `  ${briefs}/phase7_5_verification.md    Phase 7.5 (close false positives via Lucene fulltext on :Page.text)`,
    `  ${briefs}/phase8_asset_audit.md       Phase 8  (mandatory checklist as :Asset.mandatory_checklist JSON)`,
    `  ${briefs}/phase9_consolidation.md     Phase 9  (consolidate :Finding statuses)`,
    `  ${briefs}/phase10_export.md           Phase 10 (graph_export.json + restore.cypher + tier_views.cypher) — FINAL phase`,
    '',
    'There is no `phase_viz`. Visualisation is delegated to Neo4j Browser at',
    'http://localhost:7474 — once Phase 10 finishes the orchestrator hands the',
    'user a pre-filled Cypher query scoped to this asset_id. Do NOT generate',
    'asset_graph.html or any panel HTML.',
    '',
    'Reference files — load when a phase says to:',
    `  ${refs}/document_types.md            (closed enum of document_type strings)`,
    `  ${refs}/tiers_and_ata.md             (ATA→tier mapping; consumed at viz time, not as graph nodes)`,
    `  ${cypher}/schema.cypher              (constraints + page_text fulltext index — already applied at startup)`,
    `  ${cypher}/captions.cypher            (caption helper — Phase 10 auto-applies)`,
    `  ${refs}/data_quality_rules.md        (universal rules + aviation domain patterns)`,
    `  ${refs}/investigation_discipline.md  (DO NOT flag what you have not looked for)`,
    `  ${refs}/finding_types.md             (the exact finding_type strings)`,
    `  ${refs}/severity_matrix.md           (criticality-by-component)`,
    '',
    'The DAL package is at /app/sparengine-export/graph_dal/. Every phase script',
    'starts with the bootstrap shown in OVERVIEW.md "DAL CHOKEPOINT" section,',
    'imports the writers it needs, and ends by calling `verify_phase_N` (or',
    '`verify_no_fact_orphans`). If you see a Phase script in this workdir doing',
    '`import sqlite3` or writing to a `graph.db` file, that script is OBSOLETE',
    '(legacy from before the Neo4j migration). Delete it and rewrite from the brief.',
    '',
    'Reference implementation: `/app/csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phaseN.py`',
    'are the verified-working canonical AW139 phase scripts. Mirror their structure.',
    '',
    'Write per-asset deliverables into the CURRENT WORKING DIRECTORY:',
    '  ./asset_profile.json   (Phase 0)',
    '  ./graph_export.json    (Phase 10 — lossless graph projection)',
    '  ./restore.cypher       (Phase 10 — sanitised replayable Cypher)',
    '  ./tier_views.cypher    (Phase 10 — saved Browser favourites)',
    '  ./progress.log         (every phase appends a verification block)',
    '  ./_checkpoints/        (resumable checkpoints between phases)',
    '',
    'Use Python via Bash. Use pathlib.Path. Never reference graph.db or sqlite3.',
    '',
    'SHELL HYGIENE (read OVERVIEW.md "SHELL HYGIENE" section):',
    '  - DO NOT use `python -c "<multi-line script>"`. Write a .py file and run it.',
    '    Cross-platform quoting is unreliable; this is the #1 cause of exit code 2.',
    '  - All .py files: UTF-8 without BOM, ASCII quotes/dashes only, 4-space indent.',
    '',
    'CRITICAL — ANTI-FRAUD CONTRACT:',
    '',
    '1. Each phase file ends with MANDATORY VERIFICATION via the DAL\'s `verify_*`',
    '   functions. After running the phase, CALL `verify_phase_N(driver, asset_id)`',
    '   (or `verify_no_fact_orphans` if the dedicated verifier doesn\'t exist yet),',
    '   APPEND THE COUNTS to ./progress.log, and CHECK each STOP condition.',
    '   If `VerificationFailed` is raised, STOP. Do not proceed to the next phase.',
    '   Do not write a "dummy" version. Diagnose the failure first.',
    '',
    '2. Do NOT hardcode asset_profile.json from the folder name or CSV',
    '   filename. The profile must be derived by reading ≤30 representative',
    '   pages from the CSV (Phase 0 rules).',
    '',
    '3. Do NOT write graph_export.json as a Python dict literal. It is a',
    '   query result. If your phase10_export.py contains a literal',
    '   `export_data = { "asset": {...}, "nodes": [...] }` it is wrong.',
    '',
    '4. Do NOT write a function called `run_dummy_phases`. There is no such',
    '   thing as a "dummy phase". Every phase is real or it is skipped (and',
    '   skipped means STOP — do not produce stub output).',
    '',
    '5. VISUALISATION — Neo4j Browser is the canonical UI. After Phase 10',
    '   completes the orchestrator emits a `graph-ready` SSE event with a',
    '   pre-filled Cypher query (`MATCH (n {asset_id: ...}) RETURN n LIMIT 300`).',
    '   Do NOT build asset_graph.html, do NOT touch any panel template — just',
    '   write graph_export.json + restore.cypher + tier_views.cypher and stop.',
    '',
    'Do not ask for confirmation; proceed end to end. Report the verification',
    'counts to progress.log after every phase.',
    '',
    'REASONING DISCIPLINE — for every Judgement-marked phase (0, 4 rules 7-8,',
    '7, 7.5, 8, 9 — see OVERVIEW.md "CODING vs JUDGEMENT"):',
    '',
    '  1. Print a section header in your assistant text BEFORE each item:',
    '     "### [Phase N] <component_id or item>" — the UI uses this to render a banner.',
    '  2. Write 3-5 sentences of reasoning IN PLAIN ASSISTANT TEXT (not a print() in',
    '     a Python script). State your expectation, what you found, what you did',
    '     to verify, and your conclusion.',
    '  3. Use the Read tool on at least one evidence page before raising any L1 finding.',
    '  4. Append one line per item to ./decisions.log in the format documented in',
    '     OVERVIEW.md. The orchestrator counts these lines as a quality gate.',
    '  5. findings.description must be 80+ chars and cite (file: ..., page: ...).',
    '     One-liner descriptions are rejected by Phase 9 verification.',
    '',
    'Mimic an aerospace expert\'s mental model: think about what a Part-66 engineer,',
    'an MRO planner, an EASA inspector, or an asset acquisition team would want to see.',
    'A graph that just lists 472 components is not what they want. A graph that names',
    'the 5 critical items, explains their state in plain English with cited evidence,',
    'and reports the rest as supporting detail — that is what they want.',
  ].join('\n');
}

// Turn one stream-json event from Claude Code / OpenClaude into a compact
// UI event. The CLI emits these top-level shapes:
//   { type: "system",    subtype: "init", ... }
//   { type: "assistant", message: { content: [ {type:"text"|"tool_use", ...} ] } }
//   { type: "user",      message: { content: [ {type:"tool_result", content:...} ] } }
//   { type: "result",    subtype: "success"|..., result?, total_cost_usd? }
function forwardCliEvent(evt, send) {
  if (!evt || typeof evt !== 'object') return;

  if (evt.type === 'system' && evt.subtype === 'init') {
    send({ type: 'agent-init', model: evt.model, cwd: evt.cwd, tools: evt.tools });
    return;
  }

  if (evt.type === 'assistant' && evt.message?.content) {
    for (const block of evt.message.content) {
      if (block.type === 'text' && block.text) {
        // Detect "### [Phase N] <subject>" headers at the start of assistant
        // text and emit a banner event before the rest of the prose. The UI
        // renders these as prominent phase/item dividers.
        const m = block.text.match(/^###\s*\[Phase\s+([0-9.]+(?:\.[0-9]+)?)\]\s*([^\n]+)/);
        if (m) {
          send({ type: 'agent-phase', phase: m[1].trim(), subject: m[2].trim() });
          const remainder = block.text.slice(m[0].length).trim();
          if (remainder) send({ type: 'agent-reasoning', text: remainder });
        } else {
          // Heuristic: short pure-prose blocks are reasoning; long blocks (with
          // code fences) are likely status reports. Both render as text.
          send({ type: 'agent-reasoning', text: block.text });
        }
      } else if (block.type === 'tool_use') {
        send({
          type:   'agent-tool',
          tool:   block.name,
          input:  summariseToolInput(block.name, block.input),
          tool_id: block.id,
        });
      } else if (block.type === 'thinking' && block.thinking) {
        send({ type: 'agent-thinking', text: block.thinking });
      }
    }
    return;
  }

  if (evt.type === 'user' && evt.message?.content) {
    for (const block of evt.message.content) {
      if (block.type === 'tool_result') {
        const text = Array.isArray(block.content)
          ? block.content.map(c => c?.text ?? '').join('')
          : (typeof block.content === 'string' ? block.content : JSON.stringify(block.content));
        send({
          type:    'agent-tool-result',
          tool_id: block.tool_use_id,
          is_error: !!block.is_error,
          preview: truncate(text, 800),
        });
      }
    }
    return;
  }

  if (evt.type === 'result') {
    send({
      type:    'agent-result',
      subtype: evt.subtype,
      cost:    evt.total_cost_usd,
      tokens:  evt.usage,
      text:    evt.result ? truncate(evt.result, 2000) : undefined,
    });
    return;
  }

  send({ type: 'agent-log', stream: 'stdout', line: JSON.stringify(evt) });
}

function summariseToolInput(tool, input) {
  if (!input || typeof input !== 'object') return '';
  switch (tool) {
    case 'Bash':       return truncate(input.command || '', 300);
    case 'Read':       return input.file_path || '';
    case 'Write':      return input.file_path || '';
    case 'Edit':       return input.file_path || '';
    case 'Grep':       return `${input.pattern || ''}${input.path ? ' in ' + input.path : ''}`;
    case 'Glob':       return input.pattern || '';
    case 'WebFetch':   return input.url || '';
    case 'WebSearch':  return input.query || '';
    case 'TodoWrite':  return `${(input.todos || []).length} todos`;
    default:           return truncate(JSON.stringify(input), 200);
  }
}

function truncate(s, n) {
  s = String(s ?? '');
  return s.length > n ? s.slice(0, n) + '…' : s;
}

async function runAgent(workdir, csvFileName, send) {
  if (!fs.existsSync(OVERVIEW_MD)) {
    send({ type: 'agent-error',
           message: `Missing instructions file: ${OVERVIEW_MD}` });
    return;
  }

  const prompt = buildAgentPrompt(csvFileName);

  // stream-json + verbose makes the CLI emit one JSON event per line for every
  // tool call, tool result, and text delta — exactly the play-by-play we want
  // to surface in the UI.
  const streamFlags = ['--output-format=stream-json', '--verbose'];

  let cmd, args;
  if (AGENT_CLI === 'openclaude') {
    if (!fs.existsSync(CLI_MJS)) {
      send({ type: 'agent-error',
             message: 'dist/cli.mjs not found. Run `bun run build` first, or set AGENT_CLI=claude.' });
      return;
    }
    cmd  = process.execPath;
    args = [CLI_MJS, '--print', ...streamFlags, '--dangerously-skip-permissions', prompt];
  } else {
    cmd  = 'claude';
    args = ['-p', ...streamFlags, '--dangerously-skip-permissions', prompt];
  }

  send({ type: 'agent-start', cwd: workdir, cli: AGENT_CLI });

  const child = spawn(cmd, args, {
    cwd:   workdir,
    env:   process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: AGENT_CLI === 'claude' && process.platform === 'win32',
  });
  activeAgent = child;

  // Heartbeat so the UI can show "still alive" during long quiet stretches.
  const heartbeat = setInterval(() => send({ type: 'agent-heartbeat' }), 5000);

  // Parse stdout as newline-delimited JSON events from the CLI; forward each
  // one as a structured SSE event. Anything that isn't valid JSON falls
  // through as a raw log line so we don't lose data.
  let stdoutBuf = '';
  child.stdout.on('data', (buf) => {
    stdoutBuf += buf.toString('utf8');
    let nl;
    while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
      const line = stdoutBuf.slice(0, nl).trim();
      stdoutBuf = stdoutBuf.slice(nl + 1);
      if (!line) continue;
      try {
        const evt = JSON.parse(line);
        forwardCliEvent(evt, send);
      } catch {
        send({ type: 'agent-log', stream: 'stdout', line });
      }
    }
  });

  // stderr is rarely JSON; just stream lines through.
  child.stderr.on('data', (buf) => {
    for (const line of buf.toString('utf8').split(/\r?\n/)) {
      if (line.length) send({ type: 'agent-log', stream: 'stderr', line });
    }
  });

  await new Promise((resolve) => {
    child.on('close', (code, signal) => {
      clearInterval(heartbeat);
      activeAgent = null;
      send({ type: 'agent-done', code, signal });
      resolve();
    });
    child.on('error', (e) => {
      clearInterval(heartbeat);
      activeAgent = null;
      send({ type: 'agent-error', message: e.message });
      resolve();
    });
  });
}

// --- HTTP server -------------------------------------------------------------
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end',  () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.htm':  'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.csv':  'text/csv; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.svg':  'image/svg+xml',
  '.txt':  'text/plain; charset=utf-8',
  '.log':  'text/plain; charset=utf-8',
  '.db':   'application/octet-stream',
};

function safeJoin(rootDir, urlPath) {
  // Decode + normalise; reject anything that escapes the root via "..".
  const decoded = decodeURIComponent(urlPath);
  const resolved = path.resolve(rootDir, '.' + decoded);
  if (!resolved.startsWith(path.resolve(rootDir) + path.sep) &&
      resolved !== path.resolve(rootDir)) {
    return null;
  }
  return resolved;
}

function serveStatic(req, res, rootDir, urlPrefix) {
  let rel = req.url.slice(urlPrefix.length) || '/';
  const q = rel.indexOf('?'); if (q !== -1) rel = rel.slice(0, q);
  let abs = safeJoin(rootDir, rel);
  if (!abs) { res.writeHead(403); res.end('forbidden'); return; }

  fs.stat(abs, (err, st) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    if (st.isDirectory()) {
      // Try index.html, otherwise emit a minimal directory listing.
      const idx = path.join(abs, 'index.html');
      if (fs.existsSync(idx)) { abs = idx; }
      else {
        const entries = fs.readdirSync(abs, { withFileTypes: true });
        const items = entries.map(e => {
          const name = e.name + (e.isDirectory() ? '/' : '');
          const href = (rel.endsWith('/') ? rel : rel + '/') + encodeURIComponent(e.name) + (e.isDirectory() ? '/' : '');
          return `<li><a href="${urlPrefix}${href}">${name}</a></li>`;
        }).join('');
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`<!doctype html><meta charset=utf-8><title>${rel}</title>
<body style="font:14px ui-monospace,monospace;background:#0b0f17;color:#e5e7eb;padding:20px">
<h2>${rel}</h2><ul>${items}</ul></body>`);
        return;
      }
    }
    const ext = path.extname(abs).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    fs.createReadStream(abs).pipe(res);
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(indexHtml);
    return;
  }

  // Static: /csvs/* serves the per-asset folders so the user can browse
  // graph_export.json, restore.cypher, progress.log, decisions.log, etc.
  // No asset_graph.html — visualisation is delegated to Neo4j Browser.
  if (req.method === 'GET' && req.url.startsWith('/csvs/')) {
    serveStatic(req, res, path.join(repoRoot, 'csvs'), '/csvs');
    return;
  }

  // Live graph stats — polled by the UI's right rail every 5s while the
  // agent is running. Returns per-label node counts for the asset.
  // Cheap query (uses the asset_id index); empty result is silent.
  if (req.method === 'GET' && req.url.startsWith('/api/asset-stats')) {
    const url = new URL(req.url, 'http://localhost');
    const assetId = url.searchParams.get('asset_id');
    if (!assetId) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'asset_id required' }));
      return;
    }
    try {
      // Spawn a tiny python one-shot. Same pattern as runScorecard — keeps
      // the Node side free of a Neo4j driver dependency.
      const { spawn } = await import('node:child_process');
      const proc = spawn('python', ['-c',
        `import sys, json; sys.path.insert(0, "/app/sparengine-export"); ` +
        `from graph_dal import connect, database_name; ` +
        `d = connect(); ` +
        `out = {}; ` +
        `s = d.session(database=database_name()); ` +
        `rs = s.run("MATCH (n {asset_id: $aid}) UNWIND labels(n) AS l RETURN l AS label, count(*) AS n", aid="${assetId.replace(/"/g, '\\"')}"); ` +
        `[out.update({r["label"]: int(r["n"])}) for r in rs]; ` +
        `s.close(); d.close(); ` +
        `print(json.dumps(out))`,
      ], { cwd: __dirname, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
      let buf = '';
      proc.stdout.on('data', d => { buf += d.toString(); });
      proc.on('close', () => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        try { JSON.parse(buf.trim()); res.end(buf.trim()); }
        catch { res.end('{}'); }
      });
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/agent/stop') {
    if (activeAgent) {
      try {
        if (process.platform === 'win32') {
          // Best-effort hard kill on Windows; SIGTERM doesn't propagate to
          // the npm-shim'd `claude.cmd` child tree.
          spawn('taskkill', ['/pid', String(activeAgent.pid), '/T', '/F']);
        } else {
          activeAgent.kill('SIGTERM');
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ stopped: true, pid: activeAgent.pid }));
      } catch (e) {
        res.writeHead(500); res.end(e.message);
      }
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ stopped: false, reason: 'no active agent' }));
    }
    return;
  }

  if (req.method === 'POST' && req.url === '/export') {
    let body;
    try { body = JSON.parse(await readBody(req)); }
    catch { res.writeHead(400); res.end('bad json'); return; }

    const assetIds = (body.assetIds || [])
      .map(s => String(s).trim())
      .filter(Boolean);

    if (assetIds.length === 0) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'assetIds is required' }));
      return;
    }

    res.writeHead(200, {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
    });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);

    try {
      await runExport(assetIds, send, { runAgent: !!body.runAgent });
    } catch (e) {
      send({ type: 'error', message: e.message });
    }
    res.end();
    return;
  }

  // Upload a CSV directly (no Supabase pull). Multipart with:
  //   csv      — the file (required)
  //   label    — optional friendly suffix for the workdir name
  //   runAgent — "1" / "0"
  //
  // Same SSE stream shape as /export so the UI handles both identically.
  if (req.method === 'POST' && req.url === '/upload-csv') {
    res.writeHead(200, {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
    });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
    try {
      const parts = await parseMultipart(req);
      const file  = readFilePart(parts, 'csv');
      if (!file || !file.content || file.content.length === 0) {
        send({ type: 'error', message: 'No CSV file provided in form field "csv".' });
        res.end();
        return;
      }
      const label        = readPart(parts, 'label') || '';
      const runAgentFlag = (readPart(parts, 'runAgent') || '0') !== '0';

      await runFromUpload({
        csvBuffer:        file.content,
        originalFilename: file.filename,
        label,
        runAgentFlag,
        send,
      });
    } catch (e) {
      send({ type: 'error', message: e.message });
    }
    res.end();
    return;
  }

  res.writeHead(404);
  res.end('not found');
});

server.listen(PORT, () => {
  console.log(`sparengine-export listening on http://localhost:${PORT}`);
  console.log(`CSV output dir: ${repoRoot}`);
});
