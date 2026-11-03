import type { ChatSlot } from "@/hooks/useChatSocket";

/**
 * What a conversation is wired to, and why a switch is refused.
 *
 * Its own module because three components now ask the same question about the
 * same session — the header button, the dock's agent card and the panel — and
 * two of them are in different files. A component file that also exports plain
 * values cannot be hot-reloaded on its own, and "what is this chat wired to"
 * is not a component.
 */

/** Why a control is dead: both switches respawn the session underneath. */
export const BUSY_MODEL = "Finish this turn before switching model";
export const BUSY_SERVER = "Finish this turn before switching server";

/** What a conversation is wired to, whether or not it has started yet. */
export function wiring(
  slot: ChatSlot | null,
  pendingAgentKey: string,
  ambientServer: string,
) {
  return {
    agentKey: slot ? slot.info.agent_key : pendingAgentKey,
    serverName: slot ? slot.info.server_name || "" : ambientServer,
    pinned: !!slot?.info.server_pinned,
    pinnedBy: slot?.info.label || slot?.info.agent_slug || "",
  };
}
