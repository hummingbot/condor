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
 * Whose bubble this is: the agent whose page you are on, else Condor.
 *
 * Normalized, so `/agents/condor` is Condor-the-unbound-chat and not a
 * specialist that happens to share its name.
 */
export function bubbleAgentSlug(pathname: string): string {
  const m = pathname.match(/^\/agents\/([^/]+)/);
  return normalizeAgentSlug(m ? decodeURIComponent(m[1]) : "");
}
