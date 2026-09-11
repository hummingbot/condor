import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

/**
 * The home, which is the conversation (FEAT-077).
 *
 * It was briefly two views of one route: FEAT-104 hung a fleet overview off
 * `/?view=fleet` and then made it what a bare `/` means. Both halves of that
 * are undone, and now so is the page it was moved to — what every agent is
 * doing is the Execution panel of this screen's own right rail (FEAT-114),
 * read owner first, beside the conversation rather than a tab away from it.
 *
 * So `?view=` on `/` means nothing here again, and the module that forwarded
 * the old spelling is gone with the page it forwarded to: `/fleet` is still a
 * route, and it redirects into the panel, so a bookmark of either spelling
 * lands on the home with the fleet open.
 *
 * Owns the full viewport and scrolls its own transcript, so the shell drops
 * `main`'s padding for this route (see `AppShell`).
 */
export function Agents() {
  return (
    <div className="h-full min-h-0">
      <AgentChatTab />
    </div>
  );
}
