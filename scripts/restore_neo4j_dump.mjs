#!/usr/bin/env node
/**
 * Restore a Neo4j dump into the running sparengine-neo4j container's data
 * volume, overwriting the existing graph. Works on any host with `docker`
 * on PATH — Windows, macOS, Linux — and node >= 14. No bun / no TS toolchain.
 *
 * Usage (from anywhere):
 *   node scripts/restore_neo4j_dump.mjs
 *   node scripts/restore_neo4j_dump.mjs --dump /path/to/neo4j.dump
 *   node scripts/restore_neo4j_dump.mjs --container sparengine-neo4j --image neo4j:5.26-community
 *
 * What it does:
 *   1. Inspect the neo4j container to find which named volume backs /data.
 *   2. docker stop  sparengine sparengine-neo4j   (load needs the DB offline)
 *   3. docker run --rm  with neo4j-admin database load --overwrite-destination
 *   4. docker start sparengine-neo4j sparengine
 */

import { spawnSync } from 'node:child_process'
import { existsSync, statSync } from 'node:fs'
import { resolve, dirname, basename } from 'node:path'

function run(cmd, args, { capture = false, allowFail = false } = {}) {
  const pretty = `${cmd} ${args.join(' ')}`
  if (!capture) console.log(`> ${pretty}`)
  const r = spawnSync(cmd, args, {
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf-8',
    shell: false,
  })
  if (r.error) throw new Error(`spawn failed: ${pretty}\n${r.error.message}`)
  if (r.status !== 0 && !allowFail) {
    const stderr = capture ? r.stderr : ''
    throw new Error(`command failed (exit ${r.status}): ${pretty}\n${stderr}`)
  }
  return capture ? (r.stdout ?? '') : ''
}

function parseArgs(argv) {
  const opts = {
    dump: 'dumps/neo4j.dump',
    database: 'neo4j',
    image: 'neo4j:5.26-community',
    neo4jContainer: 'sparengine-neo4j',
    appContainers: ['sparengine'],
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i] ?? ''
    if (a === '--dump' || a === '-d') opts.dump = next()
    else if (a === '--database') opts.database = next()
    else if (a === '--image') opts.image = next()
    else if (a === '--container') opts.neo4jContainer = next()
    else if (a === '--app') opts.appContainers = next().split(',').filter(Boolean)
    else if (a === '-h' || a === '--help') {
      console.log(
        'Usage: restore_neo4j_dump.mjs ' +
          '[--dump <path>] [--database <name>] [--image <neo4j-image>] ' +
          '[--container <neo4j-container>] [--app <comma,separated>]',
      )
      process.exit(0)
    }
  }
  return opts
}

function findDataVolume(container) {
  const json = run('docker', ['inspect', container, '--format', '{{json .Mounts}}'], {
    capture: true,
  })
  const mounts = JSON.parse(json.trim())
  const data = mounts.find((m) => m.Destination === '/data')
  if (!data) throw new Error(`container ${container} has no /data mount`)
  if (data.Type !== 'volume') {
    // bind mount — still works; docker run -v <hostPath>:/data accepts both.
    return data.Source
  }
  return data.Name
}

function containerExists(name) {
  const out = run('docker', ['ps', '-a', '--format', '{{.Names}}'], { capture: true })
  return out.split('\n').map((s) => s.trim()).includes(name)
}

function main() {
  const opts = parseArgs(process.argv.slice(2))

  const dumpFull = resolve(process.cwd(), opts.dump)
  if (!existsSync(dumpFull) || !statSync(dumpFull).isFile()) {
    throw new Error(`Dump file not found: ${dumpFull}`)
  }
  const dumpDir = dirname(dumpFull)
  const expected = `${opts.database}.dump`
  if (basename(dumpFull) !== expected) {
    console.warn(
      `Warning: neo4j-admin expects <database>.dump (got "${basename(dumpFull)}", ` +
        `database="${opts.database}"). Rename the file or pass --database to match.`,
    )
  }

  console.log(`Dump file       : ${dumpFull}`)
  console.log(`Neo4j container : ${opts.neo4jContainer}`)
  console.log(`App containers  : ${opts.appContainers.join(', ') || '(none)'}`)

  const dataVolume = findDataVolume(opts.neo4jContainer)
  console.log(`Data volume     : ${dataVolume}`)

  const stopTargets = [opts.neo4jContainer, ...opts.appContainers].filter(containerExists)
  console.log(`\n[1/3] Stopping: ${stopTargets.join(', ')}`)
  run('docker', ['stop', ...stopTargets], { allowFail: true })

  console.log('\n[2/3] Loading dump (overwrite-destination=true)...')
  run('docker', [
    'run',
    '--rm',
    '-v',
    `${dataVolume}:/data`,
    '-v',
    `${dumpDir}:/backups:ro`,
    opts.image,
    'neo4j-admin',
    'database',
    'load',
    opts.database,
    '--from-path=/backups',
    '--overwrite-destination=true',
  ])

  console.log('\n[3/3] Starting containers back up...')
  run('docker', ['start', opts.neo4jContainer])
  for (const app of opts.appContainers) {
    if (containerExists(app)) run('docker', ['start', app])
  }

  console.log('\nDone. Neo4j Browser: http://localhost:7474')
}

try {
  main()
} catch (e) {
  console.error(`\nrestore failed: ${e.message}`)
  process.exit(1)
}
