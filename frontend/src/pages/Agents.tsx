import { Globe } from "lucide-react";
import { Link } from "react-router-dom";

import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
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
 * `main`'s padding for this route (see `AppShell`) — except for the thin bar
 * above it, the one visible door to Botcamp Hub (FEAT-153): installing and
 * publishing agents is a destination of its own now, not a configuration
 * screen, so it is reached from here rather than from Settings.
 */
export function Agents() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={`${WORKSPACE_BAR} justify-end px-3`}>
        <Link
          to="/hub"
          title="Botcamp Hub — install published agents, or publish your own"
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
        >
          <Globe className="h-3.5 w-3.5" />
          Hub
        </Link>
      </div>
      <div className="min-h-0 flex-1">
        <AgentChatTab />
      </div>
    </div>
  );
}
