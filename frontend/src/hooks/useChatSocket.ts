import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ConversationTurn } from "@/lib/api";
import { useAuth } from "@/lib/auth";
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
  /** System messages only: "switch" | "error". */
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
  /** Bound domain Agent, or "" for the plain assistant. */
  agent_slug?: string;
  /** Display name of whoever is answering. */
  label?: string;
}

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
  const [streamingSlotId, setStreamingSlotId] = useState<string | null>(null);
  const [permissionRequest, setPermissionRequest] = useState<{
    request_id: string;
    summary: string;
  } | null>(null);

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

  const connect = useCallback(() => {
    if (!token) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = import.meta.env.DEV ? "localhost:8088" : window.location.host;
    const url = `${protocol}//${host}/api/v1/ws/chat`;

    // Pass the JWT via the Sec-WebSocket-Protocol subprotocol header instead of
    // the URL query string, so it never leaks via proxy logs or history.
    const ws = new WebSocket(url, [WS_AUTH_SUBPROTOCOL, token]);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      const queued = unsent.current;
      unsent.current = [];
      for (const msg of queued) ws.send(JSON.stringify(msg));
    };
    ws.onclose = () => {
      setIsConnected(false);
      reconnectTimer.current = setTimeout(() => connect(), 3000);
    };
    ws.onmessage = (ev) => {
      try {
        handleEvent(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    };
  }, [token]);

  const disconnect = useCallback(() => {
    clearTimeout(reconnectTimer.current);
    wsRef.current?.close();
    wsRef.current = null;
    setIsConnected(false);
  }, []);

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
            setSlots((prev) => {
              // Keep whatever is already rendered for a known slot; a slot we
              // have not seen starts empty and is filled by hydrateSlot below.
              const existing = new Map(prev.map((s) => [s.info.slot_id, s]));
              return sessions.map((info) => {
                const ex = existing.get(info.slot_id);
                return ex ? { ...ex, info, pending: false } : { info, messages: [] };
              });
            });
            setActiveSlotId((prev) => {
              if (prev && sessions.some((s) => s.slot_id === prev)) return prev;
              return sessions[0].slot_id;
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
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              const msgs = [...s.messages];
              const curId = currentAssistantMsg.current[slotId];
              if (!curId) {
                const id = nextMsgId();
                currentAssistantMsg.current[slotId] = id;
                msgs.push({ id, role: "assistant", text, toolCalls: [] });
              } else {
                const idx = msgs.findIndex((m) => m.id === curId);
                if (idx >= 0) msgs[idx] = { ...msgs[idx], text: msgs[idx].text + text };
              }
              return { ...s, messages: msgs };
            }),
          );
          setStreamingSlotId(slotId);
          break;
        }

        case "thought_chunk": {
          if (!slotId) break;
          const text = data.text as string;
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              const msgs = [...s.messages];
              const curId = currentAssistantMsg.current[slotId];
              if (!curId) {
                const id = nextMsgId();
                currentAssistantMsg.current[slotId] = id;
                msgs.push({ id, role: "assistant", text: "", toolCalls: [], thought: text });
              } else {
                const idx = msgs.findIndex((m) => m.id === curId);
                if (idx >= 0)
                  msgs[idx] = {
                    ...msgs[idx],
                    thought: (msgs[idx].thought || "") + text,
                  };
              }
              return { ...s, messages: msgs };
            }),
          );
          setStreamingSlotId(slotId);
          break;
        }

        case "tool_call": {
          if (!slotId) break;
          const tc: ToolCall = {
            tool_call_id: data.tool_call_id as string,
            title: data.title as string,
            status: data.status as string,
          };
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              const msgs = [...s.messages];
              const curId = currentAssistantMsg.current[slotId];
              if (!curId) {
                const id = nextMsgId();
                currentAssistantMsg.current[slotId] = id;
                msgs.push({ id, role: "assistant", text: "", toolCalls: [tc] });
              } else {
                const idx = msgs.findIndex((m) => m.id === curId);
                if (idx >= 0)
                  msgs[idx] = {
                    ...msgs[idx],
                    toolCalls: [...msgs[idx].toolCalls, tc],
                  };
              }
              return { ...s, messages: msgs };
            }),
          );
          setStreamingSlotId(slotId);
          break;
        }

        case "tool_call_update": {
          if (!slotId) break;
          const tcId = data.tool_call_id as string;
          const status = data.status as string | undefined;
          setSlots((prev) =>
            prev.map((s) => {
              if (s.info.slot_id !== slotId) return s;
              const curId = currentAssistantMsg.current[slotId];
              if (!curId) return s;
              const msgs = s.messages.map((m) =>
                m.id === curId
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

        case "permission_request":
          setPermissionRequest({
            request_id: data.request_id as string,
            summary: data.summary as string,
          });
          break;

        case "prompt_done":
          if (slotId) {
            // Mark any in-flight tool calls as completed so the spinner stops
            setSlots((prev) =>
              prev.map((s) => {
                if (s.info.slot_id !== slotId) return s;
                const curId = currentAssistantMsg.current[slotId];
                if (!curId) return s;
                return {
                  ...s,
                  messages: s.messages.map((m) =>
                    m.id === curId && m.toolCalls.some((tc) => tc.status !== "completed" && tc.status !== "failed")
                      ? {
                          ...m,
                          toolCalls: m.toolCalls.map((tc) =>
                            tc.status === "completed" || tc.status === "failed"
                              ? tc
                              : { ...tc, status: "completed" },
                          ),
                        }
                      : m,
                  ),
                };
              }),
            );
            currentAssistantMsg.current[slotId] = null;
          }
          setStreamingSlotId(null);
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
          }
          setStreamingSlotId(null);
          break;
        }

        case "heartbeat":
          break;
      }
    },
    [hydrateSlot, prewarmLatest, send],
  );

  const sendMessage = useCallback(
    (slotId: string, text: string) => {
      const id = nextMsgId();
      updateSlotMessages(slotId, (msgs) => [
        ...msgs,
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
            server_name: session.server_name || undefined,
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
      // Immediately reset streaming state so the UI doesn't get stuck
      // if the backend's prompt_done event is lost or delayed
      setStreamingSlotId(null);
      currentAssistantMsg.current[slotId] = null;
      // Mark any in-flight tool calls as completed
      setSlots((prev) =>
        prev.map((s) => {
          if (s.info.slot_id !== slotId) return s;
          return {
            ...s,
            messages: s.messages.map((m) =>
              m.toolCalls.some((tc) => tc.status !== "completed" && tc.status !== "failed")
                ? {
                    ...m,
                    toolCalls: m.toolCalls.map((tc) =>
                      tc.status === "completed" || tc.status === "failed"
                        ? tc
                        : { ...tc, status: "completed" },
                    ),
                  }
                : m,
            ),
          };
        }),
      );
    },
    [send],
  );

  const resolvePermission = useCallback(
    (requestId: string, approved: boolean) => {
      send({ action: "resolve_permission", request_id: requestId, approved });
      setPermissionRequest(null);
    },
    [send],
  );

  useEffect(() => {
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, []);

  useEffect(() => {
    slotsRef.current = slots;
  }, [slots]);

  const activeSlot = slots.find((s) => s.info.slot_id === activeSlotId) || null;
  const isStreaming = streamingSlotId !== null;

  return {
    isConnected,
    slots,
    activeSlot,
    activeSlotId,
    setActiveSlotId,
    isStreaming,
    streamingSlotId,
    permissionRequest,
    connect,
    disconnect,
    sendMessage,
    startSession,
    resumeConversation,
    switchBrain,
    destroySession,
    abortPrompt,
    resolvePermission,
  };
}
