// Patch an asset_graph.html that uses fetch('./graph_export.json') so that the
// JSON is inlined as a <script type="application/json"> block. Result opens
// directly via file:// — no server needed.
//
// Usage:
//   node sparengine-export/inline_graph.mjs <folder>             — re-inline JSON
//   node sparengine-export/inline_graph.mjs --rebuild <folder>   — copy fresh
//                                                                  template over
//                                                                  the folder's
//                                                                  asset_graph.html
//                                                                  then inline JSON
//   node sparengine-export/inline_graph.mjs                       — process all
//                                                                  folders in csvs/

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot  = path.resolve(__dirname, '..');

function findSampleDataSpan(html) {
  const m = /const\s+SAMPLE_GRAPH_DATA\s*=\s*\{/.exec(html);
  if (!m) return null;
  const start = m.index;
  let i = m.index + m[0].length - 1;
  let depth = 0, inStr = false, quote = '', esc = false;
  for (; i < html.length; i++) {
    const c = html[i];
    if (inStr) {
      if (esc)              esc = false;
      else if (c === '\\')  esc = true;
      else if (c === quote) inStr = false;
      continue;
    }
    if (c === '"' || c === "'") { inStr = true; quote = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) { i++; break; }
    }
  }
  if (depth !== 0) return null;
  if (html[i] === ';') i++;
  return { start, end: i };
}

function inlineOne(folder) {
  const htmlPath = path.join(folder, 'asset_graph.html');
  const jsonPath = path.join(folder, 'graph_export.json');
  if (!fs.existsSync(htmlPath) || !fs.existsSync(jsonPath)) {
    console.log(`skip ${folder} (missing html or json)`);
    return;
  }

  const json = fs.readFileSync(jsonPath, 'utf8');
  let html   = fs.readFileSync(htmlPath, 'utf8');
  let mutated = false;

  // Form A: brace-balance scan to find the SAMPLE_GRAPH_DATA literal.
  const span = findSampleDataSpan(html);
  if (span) {
    html = html.slice(0, span.start)
         + `const SAMPLE_GRAPH_DATA = ${json.trim()};`
         + html.slice(span.end);
    mutated = true;
  }

  // Form B: fetch-based template. Accept fetch('graph_export.json') and
  // fetch('./graph_export.json'). Tolerate any .then(...) shape after the
  // fetch by replacing only the fetch(...) call, not the whole chain.
  if (/fetch\(['"](?:\.\/)?graph_export\.json['"]\)/.test(html)) {
    const safe = json.replace(/<\//g, '<\\/');
    const tag  = `<script id="graph-data" type="application/json">${safe}</script>`;
    if (html.includes('id="graph-data"')) {
      html = html.replace(/<script id="graph-data"[\s\S]*?<\/script>/, tag);
    } else {
      html = html.replace(/<script>/, tag + '\n    <script>');
    }
    // Replace just the fetch(...) call with a resolved-promise of the parsed
    // inline JSON. The existing .then(res => res.json()).then(data => ...)
    // chain still works because the resolved value is already the parsed data
    // — we keep the second .then but neutralise the first by returning the
    // value as-is.
    html = html.replace(
      /fetch\(['"](?:\.\/)?graph_export\.json['"]\)/,
      `Promise.resolve({ json: () => JSON.parse(document.getElementById('graph-data').textContent) })`,
    );
    mutated = true;
  }

  if (!mutated) {
    console.log(`skip ${folder} (no recognised data block)`);
    return;
  }

  const bak = htmlPath + '.bak';
  if (!fs.existsSync(bak)) fs.copyFileSync(htmlPath, bak);
  fs.writeFileSync(htmlPath, html, 'utf8');
  console.log(`inlined: ${path.relative(repoRoot, htmlPath)}`);
}

const args     = process.argv.slice(2);
const rebuild  = args.includes('--rebuild');
const positional = args.filter(a => !a.startsWith('--'));
const TEMPLATE = path.join(__dirname, 'asset_graph_template_panels.html');

function maybeRebuild(folder) {
  if (!rebuild) return;
  if (!fs.existsSync(TEMPLATE)) {
    console.error(`template missing: ${TEMPLATE}`);
    process.exit(1);
  }
  const dst = path.join(folder, 'asset_graph.html');
  fs.copyFileSync(TEMPLATE, dst);
  console.log(`rebuilt:  ${path.relative(repoRoot, dst)}`);
}

if (positional[0]) {
  const folder = path.resolve(positional[0]);
  maybeRebuild(folder);
  inlineOne(folder);
} else {
  const csvs = path.join(repoRoot, 'csvs');
  if (!fs.existsSync(csvs)) {
    console.error('no csvs/ folder');
    process.exit(1);
  }
  for (const e of fs.readdirSync(csvs, { withFileTypes: true })) {
    if (!e.isDirectory()) continue;
    const folder = path.join(csvs, e.name);
    maybeRebuild(folder);
    inlineOne(folder);
  }
}
