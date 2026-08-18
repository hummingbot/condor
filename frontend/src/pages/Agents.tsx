import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

/**
 * Agents is a place to talk to them.
 *
 * It used to be two: a chat and a card grid of the fleet. The grid was a second
 * door to things the conversation already reaches — an agent's strategies, its
 * brain, its routines all live on `/agents/:slug`, and its background tasks in
 * the chat's own dock — so it is gone, and the rail's live count is what leads
 * to whatever is looping.
 *
 * The chat owns the full viewport and scrolls its own transcript, so the shell
 * drops `main`'s padding for this route (see `AppShell`).
 */
export function Agents() {
  return (
    <div className="h-full min-h-0">
      <AgentChatTab />
    </div>
  );
}
