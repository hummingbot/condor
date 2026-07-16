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
 * Cursor (§4.1): the relay tracks the LAST DELIVERED ID — never a byte
 * offset — persisted atomically (tmp+rename) only after delivery. A crash
 * between append, delivery, and cursor advance yields at-least-once
 * delivery with id-based dedup (re-deliveries carry the same `id` and are
 * tagged replayed="true"); nothing is ever silently lost. A byte offset is
 * kept alongside purely as a tail-read hint. Startup catch-up is capped —
 * at most MAX_REPLAY recent entries no older than MAX_REPLAY_AGE_H — with a
 * one-line summary for anything beyond the cap (full history stays
 * queryable via mcp__condor__get_notifications).
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
  renameSync,
} from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
// Env overrides exist for tests only — production always uses the repo store.
const OUTBOX = process.env.CONDOR_OUTBOX ?? join(REPO_ROOT, 'store', 'notifications.jsonl')
const CURSOR_STATE =
  process.env.CONDOR_CHANNEL_STATE ?? join(REPO_ROOT, 'store', '.channel-relay-cursor')
const POLL_MS = 2000
const MAX_REPLAY = 25 // startup catch-up: at most this many missed entries...
const MAX_REPLAY_AGE_H = 24 // ...none older than this

const mcp = new Server(
  { name: 'condor-notifications', version: '0.2.0' },
  {
    capabilities: { experimental: { 'claude/channel': {} } },
    instructions:
      'Notifications from Condor trading agents arrive as ' +
      '<channel source="condor-notifications" agent_id="..." kind="...">. ' +
      'They are one-way status pings (session ticks, delegation results, ' +
      'executor events). Entries tagged replayed="true" arrived while no ' +
      'session was watching (or were re-delivered after a crash — dedup by ' +
      'the id meta) and are delivered on session start. React when ' +
      'actionable using the mcp__condor__* tools; otherwise briefly surface ' +
      'them to the user.',
  },
)

await mcp.connect(new StdioServerTransport())

type Cursor = { lastId: string; offset: number }

function readCursor(): Cursor | null {
  try {
    if (!existsSync(CURSOR_STATE)) return null
    const raw = readFileSync(CURSOR_STATE, 'utf-8').trim()
    // Legacy byte-offset state (pre-§4.1): treat as absent — the id-less
    // offset cannot be trusted across process interleavings.
    if (/^\d+$/.test(raw)) return null
    const parsed = JSON.parse(raw)
    if (typeof parsed.lastId === 'string') {
      return { lastId: parsed.lastId, offset: Number(parsed.offset ?? 0) }
    }
    return null
  } catch {
    return null
  }
}

function persistCursor(c: Cursor) {
  // Atomic tmp+rename (§4.1): a crash mid-write never corrupts the cursor.
  try {
    const tmp = `${CURSOR_STATE}.tmp${process.pid}`
    writeFileSync(tmp, JSON.stringify(c))
    renameSync(tmp, CURSOR_STATE)
  } catch (e) {
    console.error(`condor-channel: could not persist cursor: ${e}`)
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
      /* skip malformed/torn line */
    }
  }
  return { entries, rest }
}

async function deliver(entry: Record<string, unknown>, replayed = false) {
  // meta keys must be identifier-safe; values must be strings
  const meta: Record<string, string> = {
    id: String(entry.id ?? ''),
    agent_id: String(entry.agent_id ?? ''),
    kind: String(entry.kind ?? 'info'),
    ts: String(entry.ts ?? ''),
  }
  if (replayed) meta.replayed = 'true'
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content: String(entry.text ?? ''), meta },
  })
}

// Resolve the start position by ID: read the whole outbox once at startup,
// resume strictly after the last delivered id (unknown/absent id → capped
// replay of the fresh tail, same as first run).
const size0 = existsSync(OUTBOX) ? statSync(OUTBOX).size : 0
const saved = readCursor()
let lastId = saved?.lastId ?? ''
let offset = 0
let partial = ''

if (size0 > 0) {
  try {
    const { entries } = parseLines(readRange(0, size0))
    let pending = entries
    if (lastId) {
      const idx = entries.findIndex((e) => String(e.id ?? '') === lastId)
      if (idx >= 0) pending = entries.slice(idx + 1)
    }
    const minTs = Date.now() / 1000 - MAX_REPLAY_AGE_H * 3600
    const fresh = pending.filter((e) => Number(e.ts ?? 0) >= minTs)
    const replay = fresh.slice(-MAX_REPLAY)
    const skipped = pending.length - replay.length
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
    for (const e of replay) {
      await deliver(e, true)
      if (e.id) {
        lastId = String(e.id)
        persistCursor({ lastId, offset: size0 })
      }
    }
  } catch (e) {
    console.error(`condor-channel catch-up error: ${e}`)
  }
}
offset = size0
persistCursor({ lastId, offset })

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
    for (const entry of entries) {
      await deliver(entry)
      // Cursor advances AFTER delivery, one entry at a time (§4.1): a crash
      // in between re-delivers that single entry with the same id.
      if (entry.id) {
        lastId = String(entry.id)
        persistCursor({ lastId, offset })
      }
    }
  } catch (e) {
    // Never crash the channel on a bad poll; next tick retries.
    console.error(`condor-channel poll error: ${e}`)
  }
}

setInterval(poll, POLL_MS)
