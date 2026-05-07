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
}

export interface ChatSlot {
  info: SlotInfo;
  messages: ChatMessage[];
}

let msgIdCounter = 0;
function nextMsgId(): string {
  return `msg_${++msgIdCounter}`;
}

export function useChatSocket() {
  const { token } = useAuth();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Track current assistant message per slot
  const currentAssistantMsg = useRef<Record<string, string | null>>({});

  const [isConnected, setIsConnected] = useState(false);
  const [slots, setSlots] = useState<ChatSlot[]>([]);
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
          if (sessions.length > 0) {
            setSlots((prev) => {
              // Merge: keep existing messages for known slots, add new ones
              const existing = new Map(prev.map((s) => [s.info.slot_id, s]));
              const merged = sessions.map((info) => {
                const ex = existing.get(info.slot_id);
                return ex ? { ...ex, info } : { info, messages: [] };
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
          };
          setSlots((prev) => [...prev, { info: newSlot, messages: [] }]);
          setActiveSlotId(newSlot.slot_id);
          break;
        }

        case "session_destroyed": {
          const destroyedId = data.slot_id as string;
          setSlots((prev) => prev.filter((s) => s.info.slot_id !== destroyedId));
          setActiveSlotId((prev) => {
            if (prev === destroyedId) return null;
            return prev;
          });
          // Fix: select another slot if active was destroyed
          setSlots((prev) => {
            setActiveSlotId((cur) => {
              if (cur === destroyedId || cur === null) {
                return prev.length > 0 ? prev[0].info.slot_id : null;
              }
              return cur;
            });
            return prev;
          });
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
            currentAssistantMsg.current[slotId] = null;
          }
          setStreamingSlotId(null);
          break;

        case "error":
          setStreamingSlotId(null);
          break;

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
    (agentKey: string, mode: string) => {
      send({ action: "start_session", agent_key: agentKey, mode });
    },
    [send],
  );

  const destroySession = useCallback(
    (slotId: string) => {
      currentAssistantMsg.current[slotId] = null;
      send({ action: "destroy_session", slot_id: slotId });
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
    resolvePermission,
  };
}
