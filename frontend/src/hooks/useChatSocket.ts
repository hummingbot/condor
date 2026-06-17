import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { getViewContext } from "@/lib/viewContext";

export interface ToolCall {
  tool_call_id: string;
  title: string;
  status: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  toolCalls: ToolCall[];
  thought?: string;
}

export interface SlotInfo {
  slot_id: string;
  agent_key: string;
  mode: string;
  is_busy?: boolean;
  server_name?: string;
}

export interface ChatSlot {
  info: SlotInfo;
  messages: ChatMessage[];
}

let msgIdCounter = 0;
function nextMsgId(): string {
  return `msg_${Date.now()}_${++msgIdCounter}`;
}

// ── localStorage persistence for chat messages ──
const STORAGE_KEY = "condor_chat_messages";
// Cap the persisted history so localStorage can't grow unbounded across
// long-running sessions (streaming chunks re-serialize on every update).
// Only the persisted copy is trimmed; the in-memory `slots` stay intact.
const MAX_PERSISTED_SLOTS = 10; // keep the most recently active slots
const MAX_PERSISTED_MESSAGES_PER_SLOT = 100; // keep the last N messages per slot

function saveSlotMessages(slots: ChatSlot[]) {
  try {
    const data: Record<string, ChatMessage[]> = {};
    // Keep only the last MAX_PERSISTED_SLOTS slots that have messages.
    const withMessages = slots.filter((s) => s.messages.length > 0);
    for (const s of withMessages.slice(-MAX_PERSISTED_SLOTS)) {
      // Keep only the most recent messages per slot.
      data[s.info.slot_id] = s.messages.slice(-MAX_PERSISTED_MESSAGES_PER_SLOT);
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch { /* quota exceeded or private mode */ }
}

function loadSlotMessages(): Record<string, ChatMessage[]> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* corrupted */ }
  return {};
}

function clearStoredSlot(slotId: string) {
  try {
    const data = loadSlotMessages();
    delete data[slotId];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch { /* ignore */ }
}

export function useChatSocket() {
  const { token } = useAuth();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Track current assistant message per slot
  const currentAssistantMsg = useRef<Record<string, string | null>>({});

  const [isConnected, setIsConnected] = useState(false);
  const [slots, setSlots] = useState<ChatSlot[]>([]);
  // Mirror of the latest committed `slots` so event handlers can read the
  // current list synchronously without closing over stale state.
  const slotsRef = useRef<ChatSlot[]>([]);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  // Track whether we've received the initial sessions_list from the backend.
  // Prevents the empty initial slots from overwriting localStorage.
  const hydrated = useRef(false);
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

  const connect = useCallback(() => {
    if (!token) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = import.meta.env.DEV ? "localhost:8088" : window.location.host;
    const url = `${protocol}//${host}/api/v1/ws/chat?token=${token}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);
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

  const handleEvent = useCallback(
    (data: Record<string, unknown>) => {
      const event = data.event as string;
      const slotId = data.slot_id as string | undefined;

      switch (event) {
        case "sessions_list": {
          const sessions = data.sessions as SlotInfo[];
          hydrated.current = true;
          if (sessions.length > 0) {
            const stored = loadSlotMessages();
            setSlots((prev) => {
              // Merge: keep existing messages for known slots, restore from localStorage, or start empty
              const existing = new Map(prev.map((s) => [s.info.slot_id, s]));
              const merged = sessions.map((info) => {
                const ex = existing.get(info.slot_id);
                if (ex) return { ...ex, info };
                const restored = stored[info.slot_id];
                return { info, messages: restored || [] };
              });
              return merged;
            });
            setActiveSlotId((prev) => {
              if (prev && sessions.some((s) => s.slot_id === prev)) return prev;
              return sessions[0].slot_id;
            });
          }
          break;
        }

        case "session_started": {
          const newSlot: SlotInfo = {
            slot_id: data.slot_id as string,
            agent_key: data.agent_key as string,
            mode: data.mode as string,
            server_name: (data.server_name as string) || undefined,
          };
          setSlots((prev) => [...prev, { info: newSlot, messages: [] }]);
          setActiveSlotId(newSlot.slot_id);
          break;
        }

        case "session_destroyed": {
          const destroyedId = data.slot_id as string;
          clearStoredSlot(destroyedId);
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
          const errSlotId = slotId || null;
          // Reset current assistant message so next response creates a new bubble
          if (errSlotId) {
            currentAssistantMsg.current[errSlotId] = null;
          }
          // Show error as a system message in the chat
          const errMsg = (data.message as string) || "Unknown error";
          if (errSlotId) {
            setSlots((prev) =>
              prev.map((s) => {
                if (s.info.slot_id !== errSlotId) return s;
                const id = nextMsgId();
                return {
                  ...s,
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
    [],
  );

  const send = useCallback((msg: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

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

      send({ action: "send_message", slot_id: slotId, text: wireText });
    },
    [send, updateSlotMessages],
  );

  const startSession = useCallback(
    (agentKey: string, mode: string, serverName?: string) => {
      send({ action: "start_session", agent_key: agentKey, mode, server_name: serverName });
    },
    [send],
  );

  const destroySession = useCallback(
    (slotId: string) => {
      currentAssistantMsg.current[slotId] = null;
      clearStoredSlot(slotId);
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

  // Persist messages to localStorage on every change (but only after initial hydration
  // to avoid the empty initial state wiping saved messages before WS reconnects)
  useEffect(() => {
    slotsRef.current = slots;
    if (hydrated.current) {
      saveSlotMessages(slots);
    }
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
    destroySession,
    abortPrompt,
    resolvePermission,
  };
}
