import { CHAT_SLUG } from "@/lib/api";

/**
 * The chat's one spelling of "who is bound to this conversation".
 *
 * Condor has two names in the dashboard and they never met. The registry
 * knows it as `CHAT_SLUG` (`"condor"`) — that is the row `/agents` returns,
 * the directory page at `/agents/condor`, and the AGENT.md the Knowledge tab
 * reads. The *chat* knows it as the empty slug: a conversation is with Condor
 * precisely when it is bound to nobody, which is why the rail marks its row
 * active with `!activeSlot.info.agent_slug` and why `talkTo("")` is what its
 * click does.
 *
 * Anything that turns a URL into a chat binding therefore has to translate:
 * `/agents/condor` and `/?agent=condor` are the registry's spelling arriving
 * from a link, and left verbatim they spawn a session bound to `"condor"` —
 * a second, invisible Condor conversation that the rail can never light up
 * and that the bubble files under a different key than the one it uses on
 * every other page.
 *
 * One helper rather than a `slug === CHAT_SLUG ? "" : slug` at each call site,
 * because the call sites are the whole bug: the ones that forgot are exactly
 * where the two spellings diverged.
 */
export function normalizeAgentSlug(slug: string | null | undefined): string {
  const s = (slug ?? "").trim();
  return s === CHAT_SLUG ? "" : s;
}

/**
 * Which of my conversations with `slug` is "mine with this agent", when there
 * are several: the one already focused, else the newest.
 *
 * Position alone is not an answer — taking the *first* match sent "Open chat"
 * back to the oldest thread while the bubble on the page it came from showed
 * another, so the rail and the bubble told the user different stories about
 * which conversation they were in. Both ask here so they cannot drift again.
 * `slots` is append-ordered by `startSession` / `resumeConversation`, so the
 * last match is the one the user was most recently in.
 *
 * `slug` must already be normalized. The slot's own binding is normalized here
 * because a conversation resumed from a record written before the slugs were
 * reconciled can still carry the registry's spelling (`"condor"`).
 *
 * Structurally typed so lib/ takes no runtime dependency on the chat socket.
 */
export function slotFor<
  S extends { info: { slot_id: string; agent_slug?: string | null } },
>(slots: readonly S[], slug: string, activeSlotId: string | null): S | null {
  const mine = slots.filter(
    (s) => normalizeAgentSlug(s.info.agent_slug) === slug,
  );
  return (
    mine.find((s) => s.info.slot_id === activeSlotId) ?? mine.at(-1) ?? null
  );
}

/** `/agents/:slug` and anything under it. The index `/agents` is not a match. */
const AGENT_PAGE = /^\/agents\/([^/]+)/;

/**
 * Whose bubble this is: the agent whose page you are on, else Condor.
 *
 * Normalized, so `/agents/condor` is Condor-the-unbound-chat and not a
 * specialist that happens to share its name.
 */
export function bubbleAgentSlug(pathname: string): string {
  const m = pathname.match(AGENT_PAGE);
  return normalizeAgentSlug(m ? decodeURIComponent(m[1]) : "");
}

/**
 * Is this an agent's own page — the route where the bubble's bound agent and
 * the workspace's conversation are the same counterpart?
 *
 * Not answerable from `bubbleAgentSlug(pathname) !== ""`, and that is the
 * whole reason this exists: normalization collapses `/agents/condor` onto the
 * empty slug, which is also what `/bots` and `/portfolio` produce. The two
 * must not be confused — on an agent page the bubble adopts the live
 * conversation with that agent, and off it the bubble deliberately does not
 * (FEAT-059: a quick question from /bots must not land in a deep specialist
 * chat).
 */
export function isAgentPage(pathname: string): boolean {
  return AGENT_PAGE.test(pathname);
}
