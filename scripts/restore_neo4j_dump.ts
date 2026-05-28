#!/usr/bin/env bun
/**
 * Restore dumps/neo4j.dump into the compose neo4j-data volume,
 * overwriting the existing graph.
 *
 * Cross-platform: works on Windows, macOS, Linux. Requires:
 *   - docker (+ compose plugin) on PATH
 *   - the compose stack defined in compose.yaml at repo root
 *
 * Usage (from repo root):
 *   bun run scripts/restore_neo4j_dump.ts
 *   bun run scripts/restore_neo4j_dump.ts --dump dumps/neo4j.dump
 *   node --experimental-strip-types scripts/restore_neo4j_dump.ts   # node 22+
 */

import { spawnSync } from 'node:child_process'
import { existsSync, statSync } from 'node:fs'
import { resolve, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

type RunOpts = { capture?: boolean; allowFail?: boolean }

function run(cmd: string, args: string[], opts: RunOpts = {}): string {
  const pretty = `${cmd} ${args.join(' ')}`
  if (!opts.capture) console.log(`> ${pretty}`)
  const r = spawnSync(cmd, args, {
    stdio: opts.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf-8',
    shell: false,
  })
  if (r.error) throw new Error(`spawn failed: ${pretty}\n${r.error.message}`)
  if (r.status !== 0 && !opts.allowFail) {
    const stderr = opts.capture ? r.stderr : ''
    throw new Error(`command failed (exit ${r.status}): ${pretty}\n${stderr}`)
  }
  return opts.capture ? (r.stdout ?? '') : ''
}

function parseArgs(argv: string[]): { dump: string; database: string; image: string } {
  const out = {
    dump: 'dumps/neo4j.dump',
    database: 'neo4j',
    image: 'neo4j:5.26-community',
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i] ?? ''
    if (a === '--dump' || a === '-d') out.dump = next()
    else if (a === '--database') out.database = next()
    else if (a === '--image') out.image = next()
    else if (a === '-h' || a === '--help') {
      console.log(
        'Usage: restore_neo4j_dump.ts [--dump <path>] [--database <name>] [--image <neo4j-image>]',
      )
      process.exit(0)
    }
  }
  return out
}

function resolveVolumeName(serviceVolume: string, repoRoot: string): string {
  // Ask compose for the resolved volume name (handles project prefix).
  try {
    const json = run('docker', ['compose', 'config', '--format', 'json'], {
      capture: true,
    })
    const cfg = JSON.parse(json)
    const v = cfg?.volumes?.[serviceVolume]?.name
    if (typeof v === 'string' && v.length > 0) return v
  } catch (e) {
    console.warn(`compose config lookup failed: ${(e as Error).message}`)
  }
  // Fallback: derive from the folder name like docker compose does.
  const project = basename(repoRoot)
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
  return `${project}_${serviceVolume}`
}

function main() {
  const here = dirname(fileURLToPath(import.meta.url))
  const repoRoot = resolve(here, '..')
  process.chdir(repoRoot)

  const { dump, database, image } = parseArgs(process.argv.slice(2))
  const dumpFull = resolve(repoRoot, dump)
  if (!existsSync(dumpFull) || !statSync(dumpFull).isFile()) {
    throw new Error(`Dump file not found: ${dumpFull}`)
  }
  const dumpDir = dirname(dumpFull)
  const expected = `${database}.dump`
  if (basename(dumpFull) !== expected) {
    console.warn(
      `Warning: neo4j-admin expects <database>.dump (got "${basename(dumpFull)}", database="${database}"). ` +
        `Rename the file or pass --database to match.`,
    )
  }

  console.log(`Repo root : ${repoRoot}`)
  console.log(`Dump file : ${dumpFull}`)

  const volume = resolveVolumeName('neo4j-data', repoRoot)
  console.log(`Volume    : ${volume}`)

  console.log('\n[1/4] Stopping sparengine and neo4j...')
  run('docker', ['compose', 'stop', 'sparengine', 'neo4j-init', 'neo4j'], {
    allowFail: true,
  })

  console.log('\n[2/4] Loading dump (overwrite-destination=true)...')
  run('docker', [
    'run',
    '--rm',
    '-v',
    `${volume}:/data`,
    '-v',
    `${dumpDir}:/backups:ro`,
    image,
    'neo4j-admin',
    'database',
    'load',
    database,
    '--from-path=/backups',
    '--overwrite-destination=true',
  ])

  console.log('\n[3/4] Starting neo4j...')
  run('docker', ['compose', 'up', '-d', 'neo4j'])

  console.log('\n[4/4] Reapplying schema and starting sparengine...')
  run('docker', ['compose', 'up', '-d', '--force-recreate', 'neo4j-init'])
  run('docker', ['compose', 'up', '-d', 'sparengine'])

  console.log('\nDone. Neo4j Browser: http://localhost:7474')
}

try {
  main()
} catch (e) {
  console.error(`\nrestore failed: ${(e as Error).message}`)
  process.exit(1)
}
