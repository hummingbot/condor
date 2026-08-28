import { useChat } from "@/hooks/useChat";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type {
  AgentBindingOption,
  ChatAgentOption,
  CustomProvider,
} from "@/lib/api";

import { BrainPicker, type BrainSelection } from "./BrainPicker";
import { SessionServerChip } from "./SessionServerChip";

/**
 * Who is answering, and where that answer runs.
 *
 * The strip that sits directly above the transcript: a live-socket dot, one
 * picker for the brain — a bound Agent brings its own model, so the user is
 * only ever asked one question — and the chip naming the account this chat
 * trades on.
 *
 * It lives here for the same reason `ChatThread` and `SessionServerChip` do:
 * it is the identity of the chat, not of the surface showing it, so the bound
 * identity and the "not while a turn is in flight" rule stay one rule. The
 * overlay panel that used to be its other consumer is gone; the chat workspace
 * at `/` is now the only one, and it owns the chrome around it — the rail
 * toggle, the session tabs and the Manage link.
 */
export function ChatSessionIdentity({
  slot,
  agents,
  customProviders,
  agentBindings,
  isStreaming,
  onSelectBrain,
  onSelectServer,
}: {
  /** The conversation on screen, or null when there is none yet. */
  slot: ChatSlot | null;
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  isStreaming: boolean;
  onSelectBrain: (selection: BrainSelection) => void;
  /**
   * Move the conversation to another server. Handed in for the same reason the
   * brain is: the switch can be refused, and the failure has to land in the
   * banner the thread owns — not float away as an unhandled rejection while the
   * chip keeps naming the old account (CORR-225).
   */
  onSelectServer: (serverName: string) => void;
}) {
  const chat = useChat();

  // Streaming or still spawning: both controls respawn the session underneath,
  // which would drop the turn in flight.
  const busy = !!slot && (slot.pending || isStreaming);

  return (
    <>
      {chat.isConnected && (
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
      )}
      {slot && (
        <BrainPicker
          agents={agents}
          customProviders={customProviders}
          agentBindings={agentBindings}
          selectedAgentKey={slot.info.agent_key}
          selectedAgentSlug={slot.info.agent_slug || ""}
          onSelect={onSelectBrain}
          disabled={busy}
        />
      )}
      {slot && (
        <SessionServerChip
          serverName={slot.info.server_name}
          pinned={slot.info.server_pinned}
          agentSlug={slot.info.agent_slug}
          label={slot.info.label}
          disabled={busy}
          onSelect={onSelectServer}
        />
      )}
    </>
  );
}
