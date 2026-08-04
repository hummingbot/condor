import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ConversationTurn } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { toolCallState } from "@/lib/formatters";
import { getViewContext } from "@/lib/viewContext";
import { WS_AUTH_SUBPROTOCOL } from "@/lib/websocket";

export interface ToolCall {
  tool_call_id: string;
  title: string;
  status: string;
}

export interface ChatMessage {
  id: string;
  /** A `system` message is not a bubble — it is a divider in the scrollback. */
  role: "user" | "assistant" | "system";
  text: string;
  toolCalls: ToolCall[];
  thought?: string;
  /** System messages only: "switch" | "error" | "delegation". */
  kind?: string;
}

export interface SlotInfo {
  slot_id: string;
  /** Durable conversation behind the slot. Same value as slot_id for web. */
  conversation_id?: string;
  agent_key: string;
  mode: string;
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
 * message. Anything appended after it — the next question, a handover divider —
 * ends that turn as far as the transcript is concerned, whatever the wire says.
 * Without this the id of a bubble whose `prompt_done` was missed (a WS drop
 * mid-answer, a late chunk) keeps swallowing the next answer, and that answer
 * renders *above* the question it answers.
 */
function streamTarget(msgs: ChatMessage[], curId: string | null): number {
  if (!curId) return -1;
  const last = msgs.length - 1;
  return last >= 0 && msgs[last].id === curId ? last : -1;
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

export function useChatSocket() {
  const { token, user } = useAuth();
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
  // Track current assistant message per slot
  const currentAssistantMsg = useRef<Record<string, string | null>>({});
  // Conversations already fetched, so a WS reconnect doesn't re-hydrate a slot
  // that is mid-answer and clobber the streaming bubble.
  const hydratedSlots = useRef<Set<string>>(new Set());
  // Text typed into a tab whose spawn is still in flight. Keyed by the tab's
  // id (a client_ref for a new chat, the conversation id for a resume) and
  // flushed on session_started — that queue IS the warm session.
  const outbox = useRef<Record<string, string[]>>({});
  // The dashboard prewarms the most recent conversation once per mount, never
  // per reconnect: the 3s retry loop would otherwise spawn on every failure.
  const prewarmed = useRef(false);

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
  const startStreaming = useCallback((slotId: string) => {
    setStreamingSlots((prev) => (prev[slotId] ? prev : { ...prev, [slotId]: true }));
  }, []);

  /** End streaming for *one* conversation. Never for the others. */
  const stopStreaming = useCallback((slotId: string) => {
    setStreamingSlots((prev) => {
      if (!(slotId in prev)) return prev;
      const next = { ...prev };
      delete next[slotId];
      return next;
    });
  }, []);

  /**
   * Fold one streamed fragment into the slot's open bubble.
   *
   * Every streaming event lands here, so the transcript's core invariant is
   * stated once: `streamTarget` decides whether the fragment continues the
   * bubble in progress or opens a new one, the new bubble's id becomes this
   * slot's current one, and the slot counts as streaming from its first
   * fragment. Callers supply only `patch` — how their own field grows — and a
   * fragment that opens a bubble is that same patch applied to an empty one.
   */
  const appendToStream = useCallback(
    (slotId: string, patch: (m: ChatMessage) => ChatMessage) => {
      setSlots((prev) =>
        prev.map((s) => {
          if (s.info.slot_id !== slotId) return s;
          const msgs = [...s.messages];
          const idx = streamTarget(msgs, currentAssistantMsg.current[slotId]);
          if (idx < 0) {
            const id = nextMsgId();
            currentAssistantMsg.current[slotId] = id;
            msgs.push(patch({ id, role: "assistant", text: "", toolCalls: [] }));
          } else {
            msgs[idx] = patch(msgs[idx]);
          }
          return { ...s, messages: msgs };
        }),
      );
      startStreaming(slotId);
    },
    [startStreaming],
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
            agent_key: meta?.agent_key || "",
            mode: meta?.mode || "",
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
   * One spawn on connect, for the thread the user is most likely to continue.
   *
   * Not a pool: a session's subprocess carries per-user environment, so there
   * is no user-agnostic warm process to hand out. Prewarming on *selection* —
   * and this, the implicit selection of "the chat you were last in" — is the
   * same latency win without idle processes burning the session budget.
   */
  const prewarmLatest = useCallback(() => {
    if (prewarmed.current) return;
    prewarmed.current = true;
    api
      .listConversations(1)
      .then((list) => {
        const latest = list[0];
        // Re-checked after the fetch: a session may have arrived meanwhile,
        // and prewarming on top of it would spawn a second subprocess.
        if (!latest || slotsRef.current.length > 0) return;
        resumeConversation(latest.id, {
          agent_key: latest.agent_key,
          mode: latest.mode,
          server_name: latest.server_name || undefined,
          agent_slug: latest.agent_slug,
        });
      })
      .catch(() => {
        // No history, or the API is down. Either way the panel still works.
      });
  }, [resumeConversation]);

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
            mode: data.mode as string,
            server_name: (data.server_name as string) || undefined,
            server_pinned: Boolean(data.server_pinned),
            agent_slug: (data.agent_slug as string) || "",
            label: (data.label as string) || undefined,
          };
          // The optimistic tab this session belongs to: a new chat is found by
          // the ref it was opened under, a resume by the conversation itself.
          const ref = (data.client_ref as string) || "";
          const tabId = ref || newSlot.slot_id;
          setSlots((prev) =>
            prev.some((s) => s.info.slot_id === tabId)
              ? prev.map((s) =>
                  s.info.slot_id === tabId
                    ? { ...s, info: newSlot, pending: false }
                    : s,
                )
              : [...prev, { info: newSlot, messages: [] }],
          );
          setActiveSlotId((cur) => (cur === tabId || cur === null ? newSlot.slot_id : cur));

          // Anything typed while the spawn was in flight goes out now, in
          // order. The bubbles are already on screen; only the wire lagged.
          const queued = outbox.current[tabId] || [];
          delete outbox.current[tabId];
          for (const text of queued) {
            send({ action: "send_message", slot_id: newSlot.slot_id, text });
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
          const text = data.text as string;
          appendToStream(slotId, (m) => ({ ...m, text: m.text + text }));
          break;
        }

        case "thought_chunk": {
          if (!slotId) break;
          const text = data.text as string;
          appendToStream(slotId, (m) => ({
            ...m,
            thought: (m.thought || "") + text,
          }));
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
            },
          }));
          break;
        }

        case "prompt_done":
          if (slotId) {
            // Mark any in-flight tool calls as completed so the spinner stops
            setSlots((prev) =>
              prev.map((s) =>
                s.info.slot_id === slotId
                  ? { ...s, messages: settleToolCalls(s.messages) }
                  : s,
              ),
            );
            currentAssistantMsg.current[slotId] = null;
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
          // Reset current assistant message so next response creates a new bubble
          if (errSlotId) {
            currentAssistantMsg.current[errSlotId] = null;
          }
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

        case "heartbeat":
          break;
      }
    },
    [appendToStream, hydrateSlot, prewarmLatest, send, stopStreaming],
  );

  const sendMessage = useCallback(
    (slotId: string, text: string) => {
      const id = nextMsgId();
      // A question closes whatever bubble was open. The wire is supposed to say
      // so with `prompt_done`, but that event can be missed (a WS drop
      // mid-answer) — and a stale pointer would file the *next* answer into a
      // bubble sitting above this message, reading as an answer given before it
      // was asked.
      currentAssistantMsg.current[slotId] = null;
      updateSlotMessages(slotId, (msgs) => [
        ...settleToolCalls(msgs),
        { id, role: "user" as const, text, toolCalls: [] },
      ]);

      // Inject report context if the user is viewing a report
      const ctx = getViewContext();
      const wireText = ctx
        ? `${text}\n\n[System: The user is currently viewing the report file: ${ctx.filename}. If the question might relate to this report, you can read it for context.]`
        : text;

      // A tab whose spawn is still in flight has no id the backend knows yet,
      // so the message waits here rather than being sent into the void.
      const slot = slotsRef.current.find((s) => s.info.slot_id === slotId);
      if (slot?.pending) {
        (outbox.current[slotId] ||= []).push(wireText);
        return;
      }

      send({ action: "send_message", slot_id: slotId, text: wireText });
    },
    [send, updateSlotMessages],
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
    (agentKey: string, mode: string, serverName?: string, agentSlug?: string): string => {
      const ref = nextClientRef();
      const slot: ChatSlot = {
        info: {
          slot_id: ref,
          agent_key: agentKey,
          mode,
          server_name: serverName,
          agent_slug: agentSlug || "",
        },
        messages: [],
        pending: true,
      };
      slotsRef.current = [...slotsRef.current, slot];
      setSlots((prev) => [...prev, slot]);
      setActiveSlotId(ref);
      hydratedSlots.current.add(ref);
      prewarmed.current = true; // An explicit start is the warm session.
      send({
        action: "start_session",
        agent_key: agentKey,
        mode,
        server_name: serverName,
        agent_slug: agentSlug,
        client_ref: ref,
      });
      return ref;
    },
    [send],
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
      setSlots((prev) =>
        prev.map((s) => {
          if (s.info.slot_id !== slotId) return s;
          const info: SlotInfo = {
            ...s.info,
            agent_key: session.agent_key,
            mode: session.mode,
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
    [user],
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
    [user],
  );

  const destroySession = useCallback(
    (slotId: string) => {
      currentAssistantMsg.current[slotId] = null;
      delete outbox.current[slotId];
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
      // Immediately reset this slot's streaming state so the UI doesn't get
      // stuck if the backend's prompt_done event is lost or delayed. Aborting
      // one conversation says nothing about the others.
      stopStreaming(slotId);
      currentAssistantMsg.current[slotId] = null;
      // Mark any in-flight tool calls as completed
      setSlots((prev) =>
        prev.map((s) =>
          s.info.slot_id === slotId
            ? { ...s, messages: settleToolCalls(s.messages) }
            : s,
        ),
      );
    },
    [send, stopStreaming],
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
  // What the conversation on screen is being asked to approve — and nothing
  // else. A request raised elsewhere stays in the map, where the tab strip
  // badges it, so it is visible without being answerable from the wrong chat.
  const permissionRequest =
    (activeSlotId ? permissionRequests[activeSlotId] : undefined) ||
    permissionRequests[UNATTRIBUTED] ||
    null;

  return {
    isConnected,
    slots,
    activeSlot,
    activeSlotId,
    setActiveSlotId,
    isStreaming,
    streamingSlots,
    isSlotStreaming,
    permissionRequest,
    permissionRequests,
    connect,
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
