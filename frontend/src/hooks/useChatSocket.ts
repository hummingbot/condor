import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useServer } from "@/hooks/useServer";
import {
  api,
  type AppNotification,
  type ConversationTurn,
  type NotificationsResponse,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { toolCallState } from "@/lib/formatters";
import { collectViewFacts, renderViewBlock } from "@/lib/viewFacts";
import { WS_AUTH_SUBPROTOCOL } from "@/lib/websocket";

export interface ToolCall {
  tool_call_id: string;
  title: string;
  status: string;
}

/** What each key shape means, said once per conversation (FEAT-056).
 *
 * The two `redacted` kinds describe text the user can see is gone. The other
 * two describe text that is still there on purpose: a 64-byte value is a
 * transaction hash or signature far more often than a key here, and redacting
 * those by default would break "check this tx" on every use. */
const SECRET_NOTICES: Record<string, string> = {
  mnemonic:
    "**A recovery phrase was removed from that message.** It never reached the " +
    "model and was not written to the transcript. Import wallets from Settings " +
    "instead — that flow is the only one that should ever see a key. If the " +
    "phrase holds funds, move them.",
  "solana-keypair":
    "**A keypair array was removed from that message.** It never reached the " +
    "model and was not written to the transcript. Import wallets from Settings " +
    "instead. If that key holds funds, move them.",
  "evm-hex64":
    "That message carried a `0x` value 64 hex digits long. An EVM private key " +
    "looks exactly like that — and so does a transaction hash, which is why it " +
    "was passed through untouched. If it was a key, treat it as exposed: it " +
    "reached the model and the transcript.",
  "solana-b58-64":
    "That message carried an 87–88 character base58 value. A Solana secret key " +
    "looks exactly like that — and so does a transaction signature, which is " +
    "why it was passed through untouched. If it was a key, treat it as " +
    "exposed: it reached the model and the transcript.",
};

export interface ChatMessage {
  id: string;
  /** A `system` message is not a bubble — it is a divider in the scrollback. */
  role: "user" | "assistant" | "system";
  text: string;
  toolCalls: ToolCall[];
  thought?: string;
  /** System: "switch" | "error" | "delegation" | "resume" | "notification" |
   * "routine" | "secret_notice". */
  kind?: string;
  /**
   * The user redirected the agent while this answer was being written. The
   * partial stays on screen — its context survives into the next turn, so
   * nothing is actually lost — but it is marked so it does not read as a
   * complete answer that simply stopped making sense.
   */
  interrupted?: boolean;
  /**
   * This bubble is the one the current turn is still being written into.
   *
   * The flag lives in the transcript rather than in a ref beside it because
   * *every* decision about where a fragment goes is then made from the state
   * the fragment is folded into, inside the same updater. A pointer held
   * outside React had to be written when the updater ran and read when the
   * caller ran — two different moments — and any gap between them (a commit
   * that had not happened yet, an updater React re-ran) split one answer
   * across several bubbles.
   */
  open?: boolean;
}

export interface SlotInfo {
  slot_id: string;
  /** Durable conversation behind the slot. Same value as slot_id for web. */
  conversation_id?: string;
  agent_key: string;
  is_busy?: boolean;
  server_name?: string;
  /**
   * The bound Agent's front matter chose the server, so it is not the chat's
   * to change — the chip locks instead of offering a picker that would be
   * overruled at spawn.
   */
  server_pinned?: boolean;
  /** Bound domain Agent, or "" for the plain assistant. */
  agent_slug?: string;
  /** Display name of whoever is answering. */
  label?: string;
  /**
   * ISO timestamp of the last turn, or null for a session never prompted.
   * Only the roster carries it — it is what makes a reload land on the
   * conversation you were last in.
   */
  last_prompt_at?: string | null;
}

/** A tool call waiting for the user to approve or reject it. */
export interface PermissionRequest {
  request_id: string;
  summary: string;
  /** Which agent, on which server, raised it. Empty when unattributable. */
  origin?: string;
}

/**
 * Bucket for a request the backend did not stamp with a slot.
 *
 * Only an older server does that. Its requests are shown in whichever
 * conversation is active — the pre-CORR-101 behaviour — so a dashboard running
 * ahead of its backend still lets the user answer rather than silently
 * swallowing the approval until it times out.
 */
const UNATTRIBUTED = "";

export interface ChatSlot {
  info: SlotInfo;
  messages: ChatMessage[];
  /**
   * The tab exists but its subprocess is still spawning. The input is live
   * anyway — anything typed is queued and flushed the moment the session
   * lands, which is what makes a new chat feel warm instead of loading.
   */
  pending?: boolean;
}

let msgIdCounter = 0;
function nextMsgId(): string {
  return `msg_${Date.now()}_${++msgIdCounter}`;
}

/**
 * Where the next streamed fragment goes, or -1 for "open a new bubble".
 *
 * The bubble being streamed into only counts while it is still the *last*
 * message and its turn has not ended. Anything appended after it — the next
 * question, a handover divider — ends that turn as far as the transcript is
 * concerned, whatever the wire says. Without the position check a bubble whose
 * `prompt_done` was missed (a WS drop mid-answer, a late chunk) keeps
 * swallowing the next answer, and that answer renders *above* the question it
 * answers.
 */
function streamTarget(msgs: ChatMessage[]): number {
  const last = msgs.length - 1;
  return last >= 0 && msgs[last].open ? last : -1;
}

/**
 * Fold one fragment into the slot's open bubble, opening one if needed.
 *
 * A pure function of the list it is handed: the bubble a fragment continues is
 * the one the list itself marks as open, so nothing outside React has to
 * remember which bubble that was between two commits.
 */
function foldIntoStream(
  msgs: ChatMessage[],
  patch: (m: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const out = [...msgs];
  const idx = streamTarget(out);
  if (idx < 0) {
    out.push(
      patch({ id: nextMsgId(), role: "assistant", text: "", toolCalls: [], open: true }),
    );
  } else {
    out[idx] = patch(out[idx]);
  }
  return out;
}

/**
 * End the turn: whatever is on screen is what was said.
 *
 * Every open bubble is closed, not just the last one. A bubble stops being
 * *foldable* the moment anything is appended after it, but it does not stop
 * being open: an out-of-band note (a routine outcome pushed mid-answer) lands
 * after a bubble the turn was still writing into, and that bubble is what
 * `open` has to keep describing until the turn actually ends. Closing only the
 * tail left it flagged open forever, and the next turn in the same slot lit it
 * up as live again.
 *
 * Returning the same array when there is nothing to close keeps a
 * `prompt_done` for an idle slot from re-rendering the transcript.
 */
function closeStream(msgs: ChatMessage[]): ChatMessage[] {
  if (!msgs.some((m) => m.open)) return msgs;
  return msgs.map((m) => (m.open ? { ...m, open: false } : m));
}

/** Stop every tool call that is still spinning. A prompt that ended, ended. */
function settleToolCalls(msgs: ChatMessage[]): ChatMessage[] {
  return msgs.map((m) =>
    m.toolCalls.some((tc) => toolCallState(tc.status) === "pending")
      ? {
          ...m,
          toolCalls: m.toolCalls.map((tc) =>
            toolCallState(tc.status) === "pending"
              ? { ...tc, status: "completed" }
              : tc,
          ),
        }
      : m,
  );
}

let clientRefCounter = 0;
/** Local handle for a tab that has no conversation id yet. Echoed by the
 *  backend on `session_started`, which is how the two are reconciled. */
function nextClientRef(): string {
  return `new_${++clientRefCounter}`;
}

// ── History comes from the server ──
//
// This used to keep a copy of the rendered messages in localStorage. That was
// a second truth about what was said: per-browser, keyed on a slot id that
// died with the subprocess, and invisible to Telegram. The backend now records
// every turn (FEAT-015), so the transcript is fetched, never mirrored.

function turnsToMessages(turns: ConversationTurn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  turns.forEach((turn, i) => {
    const toolCalls: ToolCall[] = (turn.tool_calls || []).map((tc) => ({
      tool_call_id: String(tc.id ?? ""),
      title: String(tc.title ?? ""),
      status: String(tc.status ?? "completed"),
    }));
    // A turn with no text and no tools is an artifact of a prompt that died
    // before producing anything; rendering it as an empty bubble is noise.
    if (!turn.text && toolCalls.length === 0) return;
    // A handover reads the same after a reload as it did live: the backend
    // records it as a system turn, so there is one source for the divider.
    const role =
      turn.role === "user" ? "user" : turn.role === "system" ? "system" : "assistant";
    messages.push({
      id: `hist_${i}_${turn.ts}`,
      role,
      text: turn.text,
      toolCalls,
      thought: turn.thought || undefined,
      kind: turn.kind || undefined,
    });
  });
  return messages;
}

/**
 * How long streamed fragments accumulate before they are committed together.
 *
 * 50ms caps the chat's commit rate at 20/sec against a wire that delivers
 * 50-200 frames/sec, and is short enough that the text still reads as arriving
 * continuously.
 */
const FLUSH_INTERVAL_MS = 50;

/**
 * The bell's cache key (FEAT-048).
 *
 * Lives here because this is where the live `notification` event is written
 * into it; `NotificationBell` reads the same key, so a pushed notice and a
 * fetched one are one list. Not server-scoped: notifications belong to the
 * user, not to whichever trading server is selected.
 */
export const NOTIFICATIONS_KEY = ["notifications"] as const;

export function useChatSocket() {
  const { token, user } = useAuth();
  const queryClient = useQueryClient();
  // Which trading server a prewarmed chat is born on. Read through a ref
  // rather than a dependency: the selection changes while the socket lives,
  // and rebuilding every callback that transitively reaches it would tear the
  // connection down with them. Seeded from localStorage on the first render,
  // so it is already right when the prewarm fires.
  const { server } = useServer();
  const serverRef = useRef(server);
  useEffect(() => {
    serverRef.current = server;
  }, [server]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Whether this hook still wants a socket. `close()` is asynchronous, so the
  // cleanup that cancels the pending retry cannot also stop the `onclose` that
  // is about to fire: without this flag that handler re-arms the very reconnect
  // we just cancelled, and an unmounted hook keeps rebuilding a socket — with
  // the logged-out session's JWT — every few seconds for the life of the tab.
  const shouldConnect = useRef(false);
  // JWT the live socket was opened with, so a token change rebinds instead of
  // riding on a connection authenticated as the previous session.
  const socketToken = useRef<string | null>(null);
  // Backoff for the retry, same shape as `CondorWebSocket`: a server that is
  // down must not be hammered once per interval by every open tab.
  const reconnectDelay = useRef(1000);
  const MAX_RECONNECT_DELAY = 30000;
  // Which bubble a slot is streaming into is not tracked here: it is the
  // `open` message in the slot's own transcript. See `foldIntoStream`.
  // Conversations already fetched, so a WS reconnect doesn't re-hydrate a slot
  // that is mid-answer and clobber the streaming bubble.
  const hydratedSlots = useRef<Set<string>>(new Set());
  // Text typed into a tab whose spawn is still in flight. Keyed by the tab's
  // id (a client_ref for a new chat, the conversation id for a resume) and
  // flushed on session_started — that queue IS the warm session. Each entry
  // carries the page context captured at *queue* time, not at flush time: the
  // block is true of the moment the user asked, and a spawn can land after
  // they have navigated away.
  const outbox = useRef<Record<string, { text: string; view: string }[]>>({});
  // A tab opened optimistically is renamed on session_started: the client_ref
  // it was started under becomes the backend's slot id. The workspace follows
  // the rename through `activeSlotId`, which this hook rewrites itself — but a
  // surface that keeps its own slot id (the bubble holds one per bound agent,
  // FEAT-059) would be left pointing at a ref no slot answers to, and its next
  // send would respawn. This map is how such a caller follows the rename.
  const refAliases = useRef<Record<string, string>>({});
  // Refs started with `focus: false`, so their session_started must not adopt
  // the slot as active even when nothing else is — the workspace would open on
  // a conversation the user never chose there.
  const unfocusedRefs = useRef<Set<string>>(new Set());
  // The dashboard prewarms the most recent conversation once per mount, never
  // per reconnect: the 3s retry loop would otherwise spawn on every failure.
  const prewarmed = useRef(false);
  // Prewarming is the chat workspace's privilege, not a side effect of holding
  // the socket open. `prewarmDeferred` is the one prewarm an empty roster asked
  // for while nobody was allowed to grant it, kept so opening the workspace
  // later is still warm.
  const prewarmAllowed = useRef(false);
  const prewarmDeferred = useRef(false);

  const [isConnected, setIsConnected] = useState(false);
  const [slots, setSlots] = useState<ChatSlot[]>([]);
  // Mirror of the latest committed `slots` so event handlers can read the
  // current list synchronously without closing over stale state.
  const slotsRef = useRef<ChatSlot[]>([]);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  // Which conversations are mid-answer, keyed by slot like every other per-slot
  // piece of state here. The socket multiplexes concurrent prompts across every
  // open tab, so as a single scalar this was cross-talk: whichever slot finished
  // first cleared it for all of them, stopping the other tabs' spinners and
  // re-enabling their composer and brain picker underneath a live prompt.
  const [streamingSlots, setStreamingSlots] = useState<Record<string, true>>({});
  // Conversations whose turn has been accepted but has not started — it is
  // waiting behind the one in front of it. Keyed by slot like everything else
  // here, and short-lived: the first fragment of the answer clears it.
  const [queuedSlots, setQueuedSlots] = useState<Record<string, true>>({});
  // Keyed by the conversation that raised it, like every other per-slot piece
  // of state here. As a single scalar this was both misattributed — the banner
  // rendered in whatever tab was open — and lossy: a second confirmation
  // overwrote the first, which then went unanswered until its TTL denied it.
  const [permissionRequests, setPermissionRequests] = useState<
    Record<string, PermissionRequest>
  >({});

  // Helpers to update a specific slot's messages
  const updateSlotMessages = useCallback(
    (slotId: string, updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
      setSlots((prev) =>
        prev.map((s) =>
          s.info.slot_id === slotId
            ? { ...s, messages: updater(s.messages) }
            : s,
        ),
      );
    },
    [],
  );

  // A slot is streaming from its first fragment until its prompt ends. Both
  // helpers return the previous object when nothing changes: chunks arrive
  // rapid-fire, and a fresh object per chunk would re-render every consumer.
  const clearQueued = useCallback((slotId: string) => {
    setQueuedSlots((prev) => {
      if (!(slotId in prev)) return prev;
      const next = { ...prev };
      delete next[slotId];
      return next;
    });
  }, []);

  const startStreaming = useCallback(
    (slotId: string) => {
      setStreamingSlots((prev) => (prev[slotId] ? prev : { ...prev, [slotId]: true }));
      // The wait is over the moment the answer starts arriving.
      clearQueued(slotId);
    },
    [clearQueued],
  );

  /** End streaming for *one* conversation. Never for the others. */
  const stopStreaming = useCallback(
    (slotId: string) => {
      setStreamingSlots((prev) => {
        if (!(slotId in prev)) return prev;
        const next = { ...prev };
        delete next[slotId];
        return next;
      });
      clearQueued(slotId);
    },
    [clearQueued],
  );

  // ── Streamed fragments are coalesced, not committed one per frame ──
  //
  // ACP emits one WS frame per model chunk, so an answer arrives at 50-200
  // frames/sec. Committing each one re-runs remark over the *entire*
  // accumulated bubble — one answer of length n costing O(n²) of parse work —
  // and re-renders every consumer of the chat context at that same rate. Text
  // and thought fragments accumulate here instead, keyed by slot, and land in
  // a single `setSlots` per window: the buffer-in-a-ref idiom `unsent` and
  // `outbox` already use in this file.
  //
  // Keyed by slot rather than kept as one buffer because the socket
  // multiplexes concurrent prompts across every open tab; a shared buffer
  // would file one conversation's tokens into another's bubble.
  const pendingChunks = useRef<Record<string, { text: string; thought: string }>>(
    {},
  );
  const flushTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  /**
   * Commit every buffered fragment, for every slot, in one pass.
   *
   * Safe to call at any point: it no-ops on an empty buffer and cancels the
   * open window, so the next fragment starts a fresh one. Every path that ends
   * a turn or appends anything else to a transcript calls it *first* —
   * buffered text has to reach its bubble before a user message, an error or a
   * handover divider takes over as the last message, or the fold would file it
   * into a new bubble sitting below them.
   *
   * `endedSlot` closes that slot's turn in the same updater that lands its
   * tail, and that ordering is the whole point: the tail belongs to the answer
   * above it, and the next answer must not continue the bubble it landed in.
   * Both facts are decided from the transcript being written, so no window
   * exists in which a fragment can be filed against a bubble that no longer
   * is — or is not yet — the open one.
   */
  const flushChunks = useCallback((endedSlot?: string) => {
    clearTimeout(flushTimer.current);
    flushTimer.current = undefined;
    const pending = pendingChunks.current;
    const hasPending = Object.keys(pending).length > 0;
    // A turn that ends still has to be closed, even with nothing buffered.
    if (!hasPending && !endedSlot) return;
    // Drained before the updater runs, so a re-invoked updater (StrictMode,
    // concurrent rendering) replays the same fragments instead of appending
    // whatever has arrived since.
    pendingChunks.current = {};
    setSlots((prev) =>
      prev.map((s) => {
        const slot = s.info.slot_id;
        const buf = pending[slot];
        const ends = slot === endedSlot;
        if (!buf && !ends) return s;
        let messages = s.messages;
        if (buf) {
          messages = foldIntoStream(messages, (m) => ({
            ...m,
            text: m.text + buf.text,
            // Left alone when nothing was buffered, so a bubble with no
            // reasoning keeps `thought` undefined rather than gaining "".
            thought: buf.thought ? (m.thought || "") + buf.thought : m.thought,
          }));
        }
        if (ends) messages = closeStream(messages);
        return messages === s.messages ? s : { ...s, messages };
      }),
    );
  }, []);

  /** Accumulate one streamed fragment. The commit happens on the next flush. */
  const bufferChunk = useCallback(
    (slotId: string, field: "text" | "thought", chunk: string) => {
      const buf = (pendingChunks.current[slotId] ||= { text: "", thought: "" });
      buf[field] += chunk;
      // The live signal is deliberately *not* deferred with the text: the
      // spinner, the locked composer and the auto-expanding thinking block all
      // key off it and should react to the first fragment. `startStreaming`
      // collapses to a no-op once the slot is marked, so this stays free.
      startStreaming(slotId);
      // Only the first fragment of a window arms the timer; the rest ride it.
      if (flushTimer.current === undefined) {
        // Wrapped rather than passed by reference: `setTimeout` hands its
        // callback arguments, and a stray one would read as `endedSlot`.
        flushTimer.current = setTimeout(() => flushChunks(), FLUSH_INTERVAL_MS);
      }
    },
    [flushChunks, startStreaming],
  );

  /**
   * Fold one streamed *event* into the slot's open bubble, immediately.
   *
   * The low-frequency half of the stream — a tool call is not what makes the
   * transcript expensive, and deferring it would only delay the spinner.
   * `streamTarget` decides whether it continues the bubble in progress or
   * opens a new one, and the slot counts as streaming from its first fragment.
   */
  const appendToStream = useCallback(
    (slotId: string, patch: (m: ChatMessage) => ChatMessage) => {
      // Whatever is buffered was received before this event and has to land
      // before it, or the two arrive in the transcript out of order.
      flushChunks();
      setSlots((prev) =>
        prev.map((s) =>
          s.info.slot_id === slotId
            ? { ...s, messages: foldIntoStream(s.messages, patch) }
            : s,
        ),
      );
      startStreaming(slotId);
    },
    [flushChunks, startStreaming],
  );

  // Actions asked for before the socket was open. "Chat" on an agent's page
  // opens the panel and starts a session in the same click, so the first frame
  // routinely predates the connection. Capped, because a socket that never
  // opens must not grow a backlog.
  const unsent = useRef<Record<string, unknown>[]>([]);
  const MAX_UNSENT = 20;

  const send = useCallback((msg: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      return;
    }
    if (unsent.current.length < MAX_UNSENT) unsent.current.push(msg);
  }, []);

  // Drop the current socket without letting its asynchronous `onclose` speak
  // for a connection we already decided to abandon.
  const closeSocket = useCallback(() => {
    clearTimeout(reconnectTimer.current);
    const ws = wsRef.current;
    wsRef.current = null;
    socketToken.current = null;
    ws?.close();
  }, []);

  const connect = useCallback(() => {
    if (!token) return;
    shouldConnect.current = true;

    const live = wsRef.current;
    if (
      live &&
      socketToken.current === token &&
      (live.readyState === WebSocket.OPEN ||
        live.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    // Either the socket is gone/closing, or it carries a stale token: in both
    // cases the old one is replaced rather than reused.
    closeSocket();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = import.meta.env.DEV ? "localhost:8088" : window.location.host;
    const url = `${protocol}//${host}/api/v1/ws/chat`;

    // Pass the JWT via the Sec-WebSocket-Protocol subprotocol header instead of
    // the URL query string, so it never leaks via proxy logs or history.
    const ws = new WebSocket(url, [WS_AUTH_SUBPROTOCOL, token]);
    wsRef.current = ws;
    socketToken.current = token;

    ws.onopen = () => {
      reconnectDelay.current = 1000;
      setIsConnected(true);
      const queued = unsent.current;
      unsent.current = [];
      for (const msg of queued) ws.send(JSON.stringify(msg));
    };
    ws.onclose = () => {
      // A socket we replaced or closed on purpose still fires `onclose`, long
      // after the hook moved on. Only the one that is currently the hook's
      // socket may report the connection down or ask for another attempt.
      if (wsRef.current !== ws) return;
      setIsConnected(false);
      if (!shouldConnect.current) return;
      reconnectTimer.current = setTimeout(() => connect(), reconnectDelay.current);
      reconnectDelay.current = Math.min(
        reconnectDelay.current * 2,
        MAX_RECONNECT_DELAY,
      );
    };
    ws.onmessage = (ev) => {
      try {
        handleEvent(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    };
  }, [token, closeSocket]);

  const disconnect = useCallback(() => {
    shouldConnect.current = false;
    closeSocket();
    setIsConnected(false);
  }, [closeSocket]);

  // Pull a slot's transcript from the server and drop it into the slot. Only
  // ever runs once per slot: the WS is authoritative for anything after.
  const hydrateSlot = useCallback(
    async (conversationId: string, slotId: string) => {
      if (!conversationId || hydratedSlots.current.has(slotId)) return;
      hydratedSlots.current.add(slotId);
      try {
        const detail = await api.getConversation(conversationId);
        const restored = turnsToMessages(detail.turns);
        if (restored.length === 0) return;
        setSlots((prev) =>
          prev.map((s) =>
            // Prepend rather than replace: a turn that streamed in while the
            // fetch was in flight must survive it.
            s.info.slot_id === slotId
              ? { ...s, messages: [...restored, ...s.messages] }
              : s,
          ),
        );
      } catch {
        // A conversation we cannot read is not worth breaking the chat over.
        hydratedSlots.current.delete(slotId);
      }
    },
    [],
  );

  /**
   * Attach a session to a conversation, showing it immediately.
   *
   * The tab and its transcript appear on this frame; the subprocess spawns
   * behind them. Already attached means "just look at it" — resuming a live
   * conversation would replace a perfectly good subprocess.
   */
  const resumeConversation = useCallback(
    (conversationId: string, meta?: Partial<SlotInfo>) => {
      if (!conversationId) return;
      const existing = slotsRef.current.find(
        (s) => s.info.slot_id === conversationId,
      );
      if (existing) {
        setActiveSlotId(conversationId);
        return;
      }

      setSlots((prev) => [
        ...prev,
        {
          info: {
            slot_id: conversationId,
            conversation_id: conversationId,
            // The record's key is a log of what answered last, so for a bound
            // conversation it is not what is about to: the server resolves the
            // Agent's *current* model. `""` lets the picker fall back to that
            // rather than flashing a model the resume will not use.
            agent_key: meta?.agent_slug ? "" : meta?.agent_key || "",
            server_name: meta?.server_name,
            agent_slug: meta?.agent_slug,
            label: meta?.label,
          },
          messages: [],
          pending: true,
        },
      ]);
      setActiveSlotId(conversationId);
      // In parallel with the spawn: the transcript is readable long before the
      // agent that will continue it is up.
      void hydrateSlot(conversationId, conversationId);
      send({ action: "resume_conversation", conversation_id: conversationId });
    },
    [hydrateSlot, send],
  );

  /**
   * Open a brand new conversation. The tab is live before the spawn is.
   *
   * Returns the tab's id so the caller can talk into it on the same tick — a
   * composer that starts the chat with its first message needs that, and
   * `slotsRef` only catches up on the next commit, so the new slot is written
   * there eagerly rather than left for `sendMessage` to miss.
   */
  const startSession = useCallback(
    (
      agentKey: string,
      serverName?: string,
      agentSlug?: string,
      opts?: {
        /**
         * `false` keeps `activeSlotId` where it is: the bubble starts sessions
         * from other pages, and stealing the focus would change which
         * conversation the workspace at `/` shows (FEAT-059).
         */
        focus?: boolean;
      },
    ): string => {
      const ref = nextClientRef();
      const slot: ChatSlot = {
        info: {
          slot_id: ref,
          agent_key: agentKey,
          server_name: serverName,
          agent_slug: agentSlug || "",
        },
        messages: [],
        pending: true,
      };
      slotsRef.current = [...slotsRef.current, slot];
      setSlots((prev) => [...prev, slot]);
      if (opts?.focus === false) unfocusedRefs.current.add(ref);
      else setActiveSlotId(ref);
      hydratedSlots.current.add(ref);
      prewarmed.current = true; // An explicit start is the warm session.
      send({
        action: "start_session",
        agent_key: agentKey,
        server_name: serverName,
        agent_slug: agentSlug,
        client_ref: ref,
      });
      return ref;
    },
    [send],
  );

  /**
   * One spawn on arrival at the workspace, so the first message never pays for
   * the spawn: the thread the user is most likely to continue, or — when there
   * is nothing to continue — an empty chat waiting for its first word.
   *
   * Not a pool: a session's subprocess carries per-user environment, so there
   * is no user-agnostic warm process to hand out. Prewarming on *selection* —
   * and this, the implicit selection of "the chat you were last in" — is the
   * same latency win without idle processes burning the session budget.
   *
   * Which is exactly why it is gated. The shell holds the socket open on every
   * route so push frames arrive wherever the user is standing, and on most of
   * those routes the roster comes back empty — prewarming there would spawn a
   * subprocess for someone who only opened /portfolio. The empty roster is
   * remembered instead, and `enablePrewarm` redeems it.
   */
  const prewarmLatest = useCallback(() => {
    if (prewarmed.current) return;
    if (!prewarmAllowed.current) {
      prewarmDeferred.current = true;
      return;
    }
    prewarmed.current = true;
    api
      .listConversations(1)
      .then(async (list) => {
        const latest = list[0];
        // Re-checked after the fetch: a session may have arrived meanwhile,
        // and prewarming on top of it would spawn a second subprocess.
        if (slotsRef.current.length > 0) return;
        if (latest) {
          resumeConversation(latest.id, {
            agent_key: latest.agent_key,
            server_name: latest.server_name || undefined,
            agent_slug: latest.agent_slug,
          });
          return;
        }
        // Nobody to pick up with — a first visit, or every conversation
        // deleted. The user is here to talk anyway, so the chat they are about
        // to write in is spawned now rather than by their first message: the
        // same warm arrival a returning user gets, minus the transcript. It
        // opens unbound, on the user's own default brain, which is exactly what
        // the hero's composer would have started (`""` here would silently
        // hand them `DEFAULT_AGENT` instead of the model they last picked).
        // The options payload is the one every chat surface already reads, on
        // its own react-query key, so this shares that fetch rather than
        // adding one.
        const options = await queryClient.fetchQuery({
          queryKey: ["session-options"],
          queryFn: api.getSessionOptions,
          staleTime: Infinity,
        });
        if (slotsRef.current.length > 0) return;
        startSession(options.default_agent, serverRef.current || undefined);
      })
      .catch(() => {
        // The API is down, or the options never came. Either way the panel
        // still works: the composer starts a session on its first message.
      });
  }, [queryClient, resumeConversation, startSession]);

  /**
   * Say that this surface is a chat.
   *
   * Only the workspace calls it. Everywhere else the connection exists to
   * receive push frames — a finished delegation, a routine's notice — and a
   * user who never asked for an agent should not be given one. Calling it also
   * redeems the prewarm an empty roster deferred, so arriving at the workspace
   * after the shell already connected is as warm as opening it cold.
   */
  const enablePrewarm = useCallback(() => {
    prewarmAllowed.current = true;
    if (!prewarmDeferred.current) return;
    prewarmDeferred.current = false;
    prewarmLatest();
  }, [prewarmLatest]);

  const handleEvent = useCallback(
    (data: Record<string, unknown>) => {
      const event = data.event as string;
      const slotId = data.slot_id as string | undefined;

      switch (event) {
        case "sessions_list": {
          const sessions = data.sessions as SlotInfo[];
          if (sessions.length > 0) {
            const known = new Set(sessions.map((s) => s.slot_id));
            // A tab opened before the socket finished connecting — "Chat" on an
            // agent's page is exactly that — is still waiting for its
            // session_started, so the server cannot list it yet. It outlives
            // this roster instead of being replaced by it, and keeps the focus:
            // the user asked for that conversation, not for the oldest one.
            const pendingLocal = slotsRef.current.filter(
              (s) => s.pending && !known.has(s.info.slot_id),
            );
            const pendingIds = new Set(pendingLocal.map((s) => s.info.slot_id));
            setSlots((prev) => {
              // Keep whatever is already rendered for a known slot; a slot we
              // have not seen starts empty and is filled by hydrateSlot below.
              const existing = new Map(prev.map((s) => [s.info.slot_id, s]));
              return [
                ...sessions.map((info) => {
                  const ex = existing.get(info.slot_id);
                  return ex ? { ...ex, info, pending: false } : { info, messages: [] };
                }),
                ...pendingLocal,
              ];
            });
            setActiveSlotId((prev) => {
              if (prev && (known.has(prev) || pendingIds.has(prev))) return prev;
              // Land on the conversation with the most recent turn rather than
              // on whatever the roster lists first — server order is not
              // recency, and the oldest chat is rarely the one you meant.
              // Comparing the ISO strings is enough; they are all UTC. The
              // roster itself is left in place: slot order is tab order, and
              // reordering tabs on every reconnect would be its own annoyance.
              const latest = sessions.reduce((best, s) =>
                (s.last_prompt_at || "") > (best.last_prompt_at || "") ? s : best,
              );
              return latest.slot_id;
            });
            for (const info of sessions) {
              void hydrateSlot(info.conversation_id || info.slot_id, info.slot_id);
            }
            prewarmed.current = true; // Live sessions already are the warm one.
          } else {
            prewarmLatest();
          }
          break;
        }

        case "session_started": {
          const newSlot: SlotInfo = {
            slot_id: data.slot_id as string,
            conversation_id: (data.conversation_id as string) || undefined,
            agent_key: data.agent_key as string,
            server_name: (data.server_name as string) || undefined,
            server_pinned: Boolean(data.server_pinned),
            agent_slug: (data.agent_slug as string) || "",
            label: (data.label as string) || undefined,
          };
          // The optimistic tab this session belongs to: a new chat is found by
          // the ref it was opened under, a resume by the conversation itself.
          const ref = (data.client_ref as string) || "";
          const tabId = ref || newSlot.slot_id;
          if (ref && ref !== newSlot.slot_id) {
            refAliases.current[ref] = newSlot.slot_id;
          }
          // `delete` doubles as the membership test: an unfocused spawn is
          // one-shot, and the set must not grow for the life of the tab.
          const adopt = ref ? !unfocusedRefs.current.delete(ref) : true;
          setSlots((prev) =>
            prev.some((s) => s.info.slot_id === tabId)
              ? prev.map((s) =>
                  s.info.slot_id === tabId
                    ? { ...s, info: newSlot, pending: false }
                    : s,
                )
              : [...prev, { info: newSlot, messages: [] }],
          );
          setActiveSlotId((cur) =>
            cur === tabId || (cur === null && adopt) ? newSlot.slot_id : cur,
          );

          // Anything typed while the spawn was in flight goes out now, in
          // order. The bubbles are already on screen; only the wire lagged.
          const queued = outbox.current[tabId] || [];
          delete outbox.current[tabId];
          for (const { text, view } of queued) {
            send({
              action: "send_message",
              slot_id: newSlot.slot_id,
              text,
              view_context: view,
            });
          }

          // A resumed conversation arrives with a transcript; a brand new one
          // is empty and hydrating it is a cheap no-op.
          if (data.restored) {
            void hydrateSlot(
              newSlot.conversation_id || newSlot.slot_id,
              newSlot.slot_id,
            );
          } else {
            hydratedSlots.current.add(newSlot.slot_id);
          }
          break;
        }

        case "session_destroyed": {
          const destroyedId = data.slot_id as string;
          hydratedSlots.current.delete(destroyedId);
          // Dropped rather than flushed: the slot is being removed, so its
          // tail has nowhere to land, and `setSlots(remaining)` below replaces
          // the state outright — a flush queued alongside it would be
          // discarded anyway. Other slots keep their buffers and their open
          // window; those flush onto the reduced list a moment later.
          delete pendingChunks.current[destroyedId];
          // The agent that was waiting on this is gone, so its approval is
          // moot — and keeping it would strand an entry under a slot that no
          // longer has a tab to answer from.
          setPermissionRequests((prev) => {
            if (!(destroyedId in prev)) return prev;
            const next = { ...prev };
            delete next[destroyedId];
            return next;
          });
          // Same reason: a slot reaped mid-answer never gets its `prompt_done`,
          // and an entry under a tab that no longer exists would keep the
          // "something is streaming" flag true forever.
          stopStreaming(destroyedId);
          // Compute the slots that remain after removal once, outside any
          // updater, so both setters below stay pure (safe under StrictMode /
          // concurrent rendering, which may invoke updaters more than once).
          const remaining = slotsRef.current.filter(
            (s) => s.info.slot_id !== destroyedId,
          );
          setSlots(remaining);
          // If the destroyed slot was active (or nothing was active), fall back
          // to the first remaining slot; otherwise keep the current selection.
          setActiveSlotId((cur) =>
            cur === destroyedId || cur === null
              ? (remaining[0]?.info.slot_id ?? null)
              : cur,
          );
          break;
        }

        case "text_chunk": {
          if (!slotId) break;
          bufferChunk(slotId, "text", data.text as string);
          break;
        }

        case "thought_chunk": {
          if (!slotId) break;
          bufferChunk(slotId, "thought", data.text as string);
          break;
        }

        case "tool_call": {
          if (!slotId) break;
          const tc: ToolCall = {
            tool_call_id: data.tool_call_id as string,
            title: data.title as string,
            status: data.status as string,
          };
          appendToStream(slotId, (m) => ({
            ...m,
            toolCalls: [...m.toolCalls, tc],
          }));
          break;
        }

        case "tool_call_update": {
          if (!slotId) break;
          const tcId = data.tool_call_id as string;
          const status = data.status as string | undefined;
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              // Addressed by the call's own id rather than by "whatever is
              // streaming": a status that lands after the bubble stopped being
              // current still belongs to the call it names.
              const msgs = s.messages.map((m) =>
                m.toolCalls.some((tc) => tc.tool_call_id === tcId)
                  ? {
                      ...m,
                      toolCalls: m.toolCalls.map((tc) =>
                        tc.tool_call_id === tcId
                          ? { ...tc, status: status || tc.status }
                          : tc,
                      ),
                    }
                  : m,
              );
              return { ...s, messages: msgs };
            }),
          );
          break;
        }

        case "permission_request": {
          // Filed under the conversation that asked, so it is only answerable
          // from there. Concurrent requests from two slots coexist instead of
          // clobbering each other.
          const askingSlot = slotId || UNATTRIBUTED;
          setPermissionRequests((prev) => ({
            ...prev,
            [askingSlot]: {
              request_id: data.request_id as string,
              summary: data.summary as string,
              origin: (data.origin as string) || "",
            },
          }));
          break;
        }

        case "prompt_interrupted": {
          // The user redirected the agent. Whatever had streamed so far is
          // committed and marked, rather than left looking like a finished
          // answer that trailed off — the alternative the old dead composer
          // avoided by never letting this happen at all.
          if (!slotId) break;
          flushChunks(slotId);
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              const msgs = settleToolCalls(s.messages);
              // The partial is the last thing the agent said. The user's new
              // message is already below it — `sendMessage` appended it before
              // the wire even carried it — so this walks back to find it.
              const idx = msgs.map((m) => m.role).lastIndexOf("assistant");
              if (idx < 0) return { ...s, messages: msgs };
              const marked = [...msgs];
              marked[idx] = { ...marked[idx], interrupted: true };
              return { ...s, messages: marked };
            }),
          );
          stopStreaming(slotId);
          break;
        }

        case "queued": {
          // Accepted, not started. Saying so is the difference between "the
          // dashboard ate my message" and "it is next in line".
          if (!slotId) break;
          setQueuedSlots((prev) => (prev[slotId] ? prev : { ...prev, [slotId]: true }));
          break;
        }

        case "system_note": {
          // Something finished in the background and wrote a note into the
          // transcript — a routine's outcome, most often. The note is already
          // recorded server-side; this only puts it on screen without a reload.
          // Appended after the buffered text so it cannot cut an answer in half.
          if (!slotId) break;
          flushChunks(slotId);
          const noteText = (data.text as string) || "";
          const noteKind = (data.kind as string) || undefined;
          if (!noteText) break;
          setSlots((prev) =>
            prev.map((s) =>
              s.info.slot_id === slotId
                ? {
                    ...s,
                    messages: [
                      ...s.messages,
                      {
                        id: nextMsgId(),
                        role: "system" as const,
                        text: noteText,
                        kind: noteKind,
                        toolCalls: [],
                      },
                    ],
                  }
                : s,
            ),
          );
          break;
        }

        case "secret_notice": {
          // The funnel found something key-shaped in what was just sent
          // (FEAT-056). A `certain` kind was already replaced before the model
          // saw it and before anything was written; an ambiguous one was left
          // alone, because in this app that shape is a transaction far more
          // often than a key. The server sends the kind, never the value, and
          // sends it at most once per conversation per kind — so the wording
          // is composed here rather than shipped over the wire.
          if (!slotId) break;
          flushChunks(slotId);
          const secretKind = data.kind as string;
          const text = SECRET_NOTICES[secretKind];
          if (!text) break;
          setSlots((prev) =>
            prev.map((s) =>
              s.info.slot_id === slotId
                ? {
                    ...s,
                    messages: [
                      ...s.messages,
                      {
                        id: nextMsgId(),
                        role: "system" as const,
                        text,
                        kind: "secret_notice",
                        toolCalls: [],
                      },
                    ],
                  }
                : s,
            ),
          );
          break;
        }

        case "prompt_done":
          if (slotId) {
            // The tail of the answer is still buffered when the prompt ends
            // inside an open window, so this is what guarantees the last
            // tokens land — and it closes the turn, which used to be a
            // separate line here. Both updaters are queued in this same batch,
            // so the settle below sees the flushed transcript.
            flushChunks(slotId);
            // Mark any in-flight tool calls as completed so the spinner stops
            setSlots((prev) =>
              prev.map((s) =>
                s.info.slot_id === slotId
                  ? { ...s, messages: settleToolCalls(s.messages) }
                  : s,
              ),
            );
            // Only this conversation ended. Another tab may still be
            // mid-answer, and its composer stays locked until its own turn is
            // done.
            stopStreaming(slotId);
          }
          break;

        case "error": {
          // A spawn that failed names the optimistic tab it could not fill;
          // everything else names the slot it was streaming into.
          const errSlotId = (data.client_ref as string) || slotId || null;
          // Whatever streamed before the failure belongs in its own bubble,
          // above the error bubble appended below — and the turn is over, so
          // the next response starts a new bubble.
          flushChunks(errSlotId || undefined);
          // Show error as a system message in the chat
          const errMsg = (data.message as string) || "Unknown error";
          if (errSlotId) {
            // Nothing is coming for a tab whose spawn failed — dropping
            // `pending` stops the input pretending the queue will drain.
            delete outbox.current[errSlotId];
            setSlots((prev) =>
              prev.map((s) => {
                if (s.info.slot_id !== errSlotId) return s;
                const id = nextMsgId();
                return {
                  ...s,
                  pending: false,
                  messages: [
                    ...s.messages,
                    { id, role: "assistant" as const, text: `⚠️ ${errMsg}`, toolCalls: [] },
                  ],
                };
              }),
            );
            stopStreaming(errSlotId);
          }
          break;
        }

        case "notification": {
          // A background task finished (FEAT-048). Addressed to the *user*, not
          // to a conversation, so it carries no slot and goes nowhere near the
          // transcript — it lands in the bell's react-query cache, which is the
          // same place `GET /notifications` fills on mount. Writing into the
          // cache rather than into local state is what lets the bell be a leaf
          // component with no wiring back up to here.
          const incoming: AppNotification = {
            id: (data.id as string) || "",
            user_id: 0,
            ts: (data.ts as number) || Date.now() / 1000,
            kind: (data.kind as string) || "system",
            text: (data.text as string) || "",
            title: (data.title as string) || null,
            link: (data.link as string) || null,
            read: false,
          };
          if (!incoming.id) break;
          queryClient.setQueryData<NotificationsResponse>(
            NOTIFICATIONS_KEY,
            (prev) => {
              // A reconnect can replay one we already hold; keyed by id so it
              // is never listed twice.
              const rest = (prev?.items ?? []).filter((n) => n.id !== incoming.id);
              const items = [incoming, ...rest].slice(0, 50);
              return { items, unread: items.filter((n) => !n.read).length };
            },
          );
          break;
        }

        case "heartbeat":
          break;
      }
    },
    [
      appendToStream,
      bufferChunk,
      flushChunks,
      hydrateSlot,
      prewarmLatest,
      queryClient,
      send,
      stopStreaming,
    ],
  );

  const sendMessage = useCallback(
    (slotId: string, text: string) => {
      const id = nextMsgId();
      // Anything still buffered was said before this question and has to be
      // committed above it — once the user's bubble is last, the tail would
      // land in a new assistant bubble *below* the question.
      //
      // The same call closes whatever bubble was open. The wire is supposed to
      // say so with `prompt_done`, but that event can be missed (a WS drop
      // mid-answer) — and a bubble left open would swallow the *next* answer
      // into a bubble sitting above this message, reading as an answer given
      // before it was asked.
      flushChunks(slotId);
      updateSlotMessages(slotId, (msgs) => [
        ...settleToolCalls(msgs),
        { id, role: "user" as const, text, toolCalls: [] },
      ]);

      // What the user is looking at while asking, rendered here so it is true
      // of this moment. It travels beside the text, never inside it: the
      // backend prepends it to this one prompt and records only the user's
      // words (FEAT-059).
      const view = renderViewBlock(collectViewFacts());

      // A tab whose spawn is still in flight has no id the backend knows yet,
      // so the message waits here rather than being sent into the void.
      const slot = slotsRef.current.find((s) => s.info.slot_id === slotId);
      if (slot?.pending) {
        (outbox.current[slotId] ||= []).push({ text, view });
        return;
      }

      send({ action: "send_message", slot_id: slotId, text, view_context: view });
    },
    [flushChunks, send, updateSlotMessages],
  );

  /**
   * Rebind the chat to a different brain, mid-conversation.
   *
   * The subprocess is replaced — ACP has no identity hot-swap — but the
   * conversation is not: the backend respawns under the same key and replays
   * the transcript, and records the handover so it shows as a divider.
   */
  const switchBrain = useCallback(
    async (slotId: string, selection: { agentSlug?: string; agentKey?: string }) => {
      if (!user) return;
      const key = `web:${user.id}:${slotId}`;
      const { session } = await api.switchSession(key, {
        agent_slug: selection.agentSlug,
        agent_key: selection.agentKey,
      });
      // The outgoing brain's last words belong above the divider that retires
      // it, not in a new bubble underneath it.
      flushChunks();
      setSlots((prev) =>
        prev.map((s) => {
          if (s.info.slot_id !== slotId) return s;
          const info: SlotInfo = {
            ...s.info,
            agent_key: session.agent_key,
            // A brain switch can move the server too: binding to an Agent that
            // pins one overrides the chat's ambient choice, and unbinding
            // hands it back. Both are read off the respawned session.
            server_name: session.server_name || undefined,
            server_pinned: session.server_pinned,
            agent_slug: session.agent_slug,
            label: session.label,
          };
          // Only a change of *who* divides the scrollback; a model swap under
          // the same identity is not a handover the reader needs marked.
          if (selection.agentSlug === undefined) return { ...s, info };
          return {
            ...s,
            info,
            messages: [
              ...s.messages,
              {
                id: nextMsgId(),
                role: "system" as const,
                text: `Switched to ${session.label}`,
                kind: "switch",
                toolCalls: [],
              },
            ],
          };
        }),
      );
    },
    [flushChunks, user],
  );

  /**
   * Move this conversation to another server, mid-chat.
   *
   * The same trade as a brain switch, for the same reason: the server is baked
   * into the MCP subprocess's args at spawn, so repointing it means reaping the
   * process and replaying the transcript into its replacement. The divider is
   * appended locally because the scrollback is not re-hydrated after a switch —
   * the backend records the same line in the transcript, so a reload agrees.
   */
  const switchServer = useCallback(
    async (slotId: string, serverName: string) => {
      if (!user) return;
      const key = `web:${user.id}:${slotId}`;
      const { session } = await api.switchSession(key, { server_name: serverName });
      // Same ordering rule as the brain switch: buffered text first, divider
      // after it.
      flushChunks();
      setSlots((prev) =>
        prev.map((s) => {
          if (s.info.slot_id !== slotId) return s;
          const info: SlotInfo = {
            ...s.info,
            server_name: session.server_name || undefined,
            server_pinned: session.server_pinned,
          };
          // Only an actual move divides the scrollback. A pinned Agent ignores
          // the request, and a divider claiming otherwise would be a lie.
          if (session.server_name === s.info.server_name) return { ...s, info };
          return {
            ...s,
            info,
            messages: [
              ...s.messages,
              {
                id: nextMsgId(),
                role: "system" as const,
                text: `Now using server ${session.server_name}`,
                kind: "switch",
                toolCalls: [],
              },
            ],
          };
        }),
      );
    },
    [flushChunks, user],
  );

  const destroySession = useCallback(
    (slotId: string) => {
      delete outbox.current[slotId];
      // The tab is going away, so its tail has nowhere to land: flushing it
      // would only write into a conversation the user just closed.
      delete pendingChunks.current[slotId];
      // A new chat closed before its conversation was minted has no id the
      // backend knows, so there is nothing to ask it to destroy. (A pending
      // *resume* does have one, and destroy_session waits for its spawn.)
      const slot = slotsRef.current.find((s) => s.info.slot_id === slotId);
      if (slot?.pending && !slot.info.conversation_id) {
        const remaining = slotsRef.current.filter((s) => s.info.slot_id !== slotId);
        setSlots(remaining);
        setActiveSlotId((cur) =>
          cur === slotId ? (remaining[0]?.info.slot_id ?? null) : cur,
        );
        return;
      }
      // Only the session goes; the transcript stays on the server, which is
      // what makes this reversible.
      send({ action: "destroy_session", slot_id: slotId });
    },
    [send],
  );

  const abortPrompt = useCallback(
    (slotId: string) => {
      send({ action: "abort_prompt", slot_id: slotId });
      // What already arrived is part of the transcript even though the user
      // stopped the rest of it; the turn ends with it.
      flushChunks(slotId);
      // Immediately reset this slot's streaming state so the UI doesn't get
      // stuck if the backend's prompt_done event is lost or delayed. Aborting
      // one conversation says nothing about the others.
      stopStreaming(slotId);
      // Mark any in-flight tool calls as completed
      setSlots((prev) =>
        prev.map((s) =>
          s.info.slot_id === slotId
            ? { ...s, messages: settleToolCalls(s.messages) }
            : s,
        ),
      );
    },
    [flushChunks, send, stopStreaming],
  );

  const resolvePermission = useCallback(
    (requestId: string, approved: boolean) => {
      send({ action: "resolve_permission", request_id: requestId, approved });
      // Only the request just answered is dropped. Clearing the whole map
      // would discard a confirmation another conversation is still waiting
      // on, leaving that agent stalled until its TTL denies it.
      setPermissionRequests((prev) => {
        const entries = Object.entries(prev).filter(
          ([, req]) => req.request_id !== requestId,
        );
        return entries.length === Object.keys(prev).length
          ? prev
          : Object.fromEntries(entries);
      });
    },
    [send],
  );

  // Unmounting the tree that hosts the provider — logging out is the one that
  // matters — must end the connection for good, not just until the next retry.
  useEffect(() => {
    return () => {
      shouldConnect.current = false;
      closeSocket();
      // Cancelled, not flushed. There is no state left to flush into — this
      // hook owns the slots and they go with it — and a timer surviving the
      // unmount would be a `setSlots` on a dead tree. Nothing is actually
      // lost: the backend records every turn (FEAT-015), so a remount
      // re-hydrates the transcript from the server rather than from here.
      clearTimeout(flushTimer.current);
      flushTimer.current = undefined;
      pendingChunks.current = {};
    };
  }, [closeSocket]);

  useEffect(() => {
    slotsRef.current = slots;
  }, [slots]);

  const activeSlot = slots.find((s) => s.info.slot_id === activeSlotId) || null;
  /** Is *this* conversation mid-answer? The only question a consumer should ask. */
  const isSlotStreaming = useCallback(
    (slotId: string | null | undefined) => !!slotId && slotId in streamingSlots,
    [streamingSlots],
  );
  /** Any conversation at all, for chrome that is not tied to one tab. */
  const isStreaming = Object.keys(streamingSlots).length > 0;
  /** Is *this* conversation waiting for the turn ahead of it to finish? */
  const isSlotQueued = useCallback(
    (slotId: string | null | undefined) => !!slotId && slotId in queuedSlots,
    [queuedSlots],
  );
  // What *one* conversation is being asked to approve — and nothing else. A
  // request raised elsewhere stays in the map, where the tab strip badges it,
  // so it is visible without being answerable from the wrong chat. A selector
  // rather than a value derived from `activeSlotId`, so a surface that is not
  // the active one — the bubble — can answer its own approvals (FEAT-059).
  const permissionFor = useCallback(
    (slotId: string | null | undefined) =>
      (slotId ? permissionRequests[slotId] : undefined) ||
      permissionRequests[UNATTRIBUTED] ||
      null,
    [permissionRequests],
  );

  /** Follow the spawn's rename: the live slot id behind a possibly-stale one. */
  const resolveSlotId = useCallback(
    (id: string): string => refAliases.current[id] ?? id,
    [],
  );

  return {
    isConnected,
    slots,
    activeSlot,
    activeSlotId,
    setActiveSlotId,
    isStreaming,
    streamingSlots,
    isSlotStreaming,
    isSlotQueued,
    permissionFor,
    permissionRequests,
    resolveSlotId,
    connect,
    enablePrewarm,
    disconnect,
    sendMessage,
    startSession,
    resumeConversation,
    switchBrain,
    switchServer,
    destroySession,
    abortPrompt,
    resolvePermission,
  };
}
