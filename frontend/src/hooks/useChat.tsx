import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";

import { useChatSocket } from "@/hooks/useChatSocket";
import {
  api,
  type AgentBindingOption,
  type ChatAgentOption,
  type CustomProvider,
} from "@/lib/api";

/**
 * One chat, however many surfaces are looking at it.
 *
 * `useChatSocket` keeps the socket, the slots, the streaming slot, the prewarm
 * guard and the outbox in a single instance. Calling it twice would open a
 * second socket, spawn a second subprocess against the session budget, and give
 * the two surfaces different ideas about what was said — so it is called once,
 * here, and both the overlay panel and the `/agents` workspace read the same
 * state. Streaming an answer in one and switching to the other shows the same
 * tokens still arriving, because there is only one of them.
 */
const ChatContext = createContext<ReturnType<typeof useChatSocket> | null>(null);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const chat = useChatSocket();
  return <ChatContext value={chat}>{children}</ChatContext>;
}

export function useChat() {
  const chat = useContext(ChatContext);
  if (!chat) throw new Error("useChat must be used within a ChatProvider");
  return chat;
}

// ── Chat options ──

export interface SessionOptions {
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  defaultAgent: string;
}

/** What the picker falls back to when `/sessions/options` cannot be read. */
const FALLBACK: SessionOptions = {
  agents: [{ key: "claude-code", label: "Claude Code" }],
  customProviders: [],
  agentBindings: [],
  defaultAgent: "claude-code",
};

/**
 * Who can answer, and on what.
 *
 * `/sessions/options` carries the picker whole: the agents and custom
 * providers that can answer, and the domain Agents a session can be bound to —
 * that is the "Agents" section. It is a near-static payload
 * every chat surface needs, so it goes through react-query on one key: fetched
 * once, shared by the panel and the workspace.
 */
export function useSessionOptions(enabled = true): SessionOptions {
  const { data } = useQuery({
    queryKey: ["session-options"],
    queryFn: api.getSessionOptions,
    staleTime: Infinity,
    enabled,
  });

  if (!data) return FALLBACK;
  return {
    agents: data.agents,
    customProviders: data.custom_providers ?? [],
    agentBindings: data.agent_bindings ?? [],
    defaultAgent: data.default_agent,
  };
}
