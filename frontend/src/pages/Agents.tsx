import { Navigate, useSearchParams } from "react-router-dom";

import { legacyFleetPath } from "@/lib/homeView";
import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

/**
 * The home, which is the conversation (FEAT-077).
 *
 * It was briefly two views of one route: FEAT-104 hung a fleet overview off
 * `/?view=fleet` and then made it what a bare `/` means. Both halves of that
 * are undone here. The overview earned a page — it is `/fleet` now, in the nav
 * beside `/floor`, reachable by typing its name instead of by remembering a
 * query parameter — and the home went back to being the thing every link,
 * notification and reflex in this product already pointed at. A route that
 * rendered two unrelated screens depending on its search string was a switch
 * nobody could see from the address bar.
 *
 * The old spelling still forwards, because it is in bookmarks and in
 * notification payloads: `legacyFleetPath` hands `/?view=fleet` to `/fleet`
 * with everything but `view` carried across.
 *
 * Owns the full viewport and scrolls its own transcript, so the shell drops
 * `main`'s padding for this route (see `AppShell`).
 */
export function Agents() {
  const [searchParams] = useSearchParams();

  const legacy = legacyFleetPath(searchParams);
  if (legacy) return <Navigate to={legacy} replace />;

  return (
    <div className="h-full min-h-0">
      <AgentChatTab />
    </div>
  );
}
