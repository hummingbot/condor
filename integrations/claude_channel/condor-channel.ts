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
 * Registered in .mcp.json; only entries appended AFTER session start are
 * pushed (history is available via mcp__condor__get_notifications).
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { existsSync, statSync, openSync, readSync, closeSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const OUTBOX = join(REPO_ROOT, 'store', 'notifications.jsonl')
const POLL_MS = 2000

const mcp = new Server(
  { name: 'condor-notifications', version: '0.1.0' },
  {
    capabilities: { experimental: { 'claude/channel': {} } },
    instructions:
      'Notifications from Condor trading agents arrive as ' +
      '<channel source="condor-notifications" agent_id="..." kind="...">. ' +
      'They are one-way status pings (session ticks, delegation results, ' +
      'executor events). React when actionable using the mcp__condor__* ' +
      'tools; otherwise briefly surface them to the user.',
  },
)

await mcp.connect(new StdioServerTransport())

// Tail from the CURRENT end of the outbox: history is Claude's to query
// via get_notifications, not to replay into the session.
let offset = existsSync(OUTBOX) ? statSync(OUTBOX).size : 0
let partial = ''

async function poll() {
  try {
    if (!existsSync(OUTBOX)) return
    const size = statSync(OUTBOX).size
    if (size < offset) {
      offset = 0 // truncated/rotated: start over from the top
      partial = ''
    }
    if (size === offset) return

    const fd = openSync(OUTBOX, 'r')
    try {
      const buf = Buffer.alloc(size - offset)
      readSync(fd, buf, 0, buf.length, offset)
      offset = size
      partial += buf.toString('utf-8')
    } finally {
      closeSync(fd)
    }

    const lines = partial.split('\n')
    partial = lines.pop() ?? '' // keep any trailing partial line
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue
      let entry: Record<string, unknown>
      try {
        entry = JSON.parse(trimmed)
      } catch {
        continue
      }
      // meta keys must be identifier-safe; values must be strings
      const meta: Record<string, string> = {
        agent_id: String(entry.agent_id ?? ''),
        kind: String(entry.kind ?? 'info'),
        ts: String(entry.ts ?? ''),
        delivered_telegram: String(entry.delivered_telegram ?? ''),
      }
      await mcp.notification({
        method: 'notifications/claude/channel',
        params: { content: String(entry.text ?? ''), meta },
      })
    }
  } catch (e) {
    // Never crash the channel on a bad poll; next tick retries.
    console.error(`condor-channel poll error: ${e}`)
  }
}

setInterval(poll, POLL_MS)
