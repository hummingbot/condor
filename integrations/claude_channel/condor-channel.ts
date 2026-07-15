#!/usr/bin/env bun
/**
 * Condor notifications channel for Claude Code.
 *
 * Tails the Condor notifications outbox (store/notifications.jsonl — every
 * agent/session/delegation ping lands there, see condor/notifications.py)
 * and pushes each NEW entry into the running Claude Code session as a
 * <channel source="condor-notifications"> event. One-way by design: the
 * session already has the full mcp__condor__* toolset to act on anything
 * it reads here.
 *
 * Run (research preview — custom channels need the dev flag):
 *   claude --dangerously-load-development-channels server:condor-notifications
 *
 * Catch-up: the delivered position is persisted to store/.channel-relay-offset,
 * so a NEW session replays entries appended while no session was watching
 * (machine asleep, session closed) instead of silently skipping them. The
 * replay is capped — at most MAX_REPLAY recent entries no older than
 * MAX_REPLAY_AGE_H — with a one-line summary for anything beyond the cap
 * (full history stays queryable via mcp__condor__get_notifications).
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  existsSync,
  statSync,
  openSync,
  readSync,
  closeSync,
  readFileSync,
  writeFileSync,
} from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
// Env overrides exist for tests only — production always uses the repo store.
const OUTBOX = process.env.CONDOR_OUTBOX ?? join(REPO_ROOT, 'store', 'notifications.jsonl')
const OFFSET_STATE =
  process.env.CONDOR_CHANNEL_STATE ?? join(REPO_ROOT, 'store', '.channel-relay-offset')
const POLL_MS = 2000
const MAX_REPLAY = 25 // startup catch-up: at most this many missed entries...
const MAX_REPLAY_AGE_H = 24 // ...none older than this

const mcp = new Server(
  { name: 'condor-notifications', version: '0.1.0' },
  {
    capabilities: { experimental: { 'claude/channel': {} } },
    instructions:
      'Notifications from Condor trading agents arrive as ' +
      '<channel source="condor-notifications" agent_id="..." kind="...">. ' +
      'They are one-way status pings (session ticks, delegation results, ' +
      'executor events). Entries tagged replayed="true" arrived while no ' +
      'session was watching and are delivered on session start. React when ' +
      'actionable using the mcp__condor__* tools; otherwise briefly surface ' +
      'them to the user.',
  },
)

await mcp.connect(new StdioServerTransport())

function readOffsetState(): number | null {
  try {
    if (!existsSync(OFFSET_STATE)) return null
    const n = parseInt(readFileSync(OFFSET_STATE, 'utf-8').trim(), 10)
    return Number.isFinite(n) && n >= 0 ? n : null
  } catch {
    return null
  }
}

function persistOffset(n: number) {
  try {
    writeFileSync(OFFSET_STATE, String(n))
  } catch (e) {
    console.error(`condor-channel: could not persist offset: ${e}`)
  }
}

function readRange(from: number, to: number): string {
  const fd = openSync(OUTBOX, 'r')
  try {
    const buf = Buffer.alloc(to - from)
    readSync(fd, buf, 0, buf.length, from)
    return buf.toString('utf-8')
  } finally {
    closeSync(fd)
  }
}

function parseLines(chunk: string): { entries: Record<string, unknown>[]; rest: string } {
  const lines = chunk.split('\n')
  const rest = lines.pop() ?? ''
  const entries: Record<string, unknown>[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      entries.push(JSON.parse(trimmed))
    } catch {
      /* skip malformed line */
    }
  }
  return { entries, rest }
}

async function deliver(entry: Record<string, unknown>, replayed = false) {
  // meta keys must be identifier-safe; values must be strings
  const meta: Record<string, string> = {
    agent_id: String(entry.agent_id ?? ''),
    kind: String(entry.kind ?? 'info'),
    ts: String(entry.ts ?? ''),
    delivered_telegram: String(entry.delivered_telegram ?? ''),
  }
  if (replayed) meta.replayed = 'true'
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content: String(entry.text ?? ''), meta },
  })
}

// Resolve the starting offset: persisted position (catch-up) when sane,
// else the current end of the outbox (first run / truncated state).
const size0 = existsSync(OUTBOX) ? statSync(OUTBOX).size : 0
const saved = readOffsetState()
let offset = saved !== null && saved <= size0 ? saved : size0
let partial = ''

// One-time startup catch-up of entries appended while no session was watching.
if (offset < size0) {
  try {
    const { entries } = parseLines(readRange(offset, size0))
    const minTs = Date.now() / 1000 - MAX_REPLAY_AGE_H * 3600
    const fresh = entries.filter((e) => Number(e.ts ?? 0) >= minTs)
    const replay = fresh.slice(-MAX_REPLAY)
    const skipped = entries.length - replay.length
    if (skipped > 0) {
      await deliver(
        {
          kind: 'info',
          text:
            `condor-channel: ${skipped} older undelivered notification(s) not ` +
            `replayed (cap ${MAX_REPLAY}/${MAX_REPLAY_AGE_H}h) — query ` +
            `mcp__condor__get_notifications for full history.`,
        },
        true,
      )
    }
    for (const e of replay) await deliver(e, true)
  } catch (e) {
    console.error(`condor-channel catch-up error: ${e}`)
  }
  offset = size0
}
persistOffset(offset)

async function poll() {
  try {
    if (!existsSync(OUTBOX)) return
    const size = statSync(OUTBOX).size
    if (size < offset) {
      offset = 0 // truncated/rotated: start over from the top
      partial = ''
    }
    if (size === offset) return

    const chunk = readRange(offset, size)
    offset = size
    const { entries, rest } = parseLines(partial + chunk)
    partial = rest
    for (const entry of entries) await deliver(entry)
    persistOffset(offset)
  } catch (e) {
    // Never crash the channel on a bad poll; next tick retries.
    console.error(`condor-channel poll error: ${e}`)
  }
}

setInterval(poll, POLL_MS)
