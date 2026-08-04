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
 * the 480 px overlay panel and the `/agents` workspace both render it, so the
 * bound identity and the "not while a turn is in flight" rule cannot come to
 * mean two different things depending on where the chat was opened. Each
 * surface still owns its own chrome around it — the panel's History/New/
 * Minimize, the workspace's rail toggle and Manage link.
 */
export function ChatSessionIdentity({
  slot,
  agents,
  customProviders,
  agentBindings,
  isStreaming,
  onSelectBrain,
  placeholder,
}: {
  /** The conversation on screen, or null when there is none yet. */
  slot: ChatSlot | null;
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  isStreaming: boolean;
  onSelectBrain: (selection: BrainSelection) => void;
  /** What stands in for the picker before there is a session to switch. */
  placeholder?: React.ReactNode;
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
      {slot ? (
        <BrainPicker
          agents={agents}
          customProviders={customProviders}
          agentBindings={agentBindings}
          selectedAgentKey={slot.info.agent_key}
          selectedAgentSlug={slot.info.agent_slug || ""}
          onSelect={onSelectBrain}
          variant="inline"
          disabled={busy}
        />
      ) : (
        placeholder
      )}
      {slot && (
        <SessionServerChip
          serverName={slot.info.server_name}
          pinned={slot.info.server_pinned}
          agentSlug={slot.info.agent_slug}
          label={slot.info.label}
          disabled={busy}
          onSelect={(name) => chat.switchServer(slot.info.slot_id, name)}
        />
      )}
    </>
  );
}
