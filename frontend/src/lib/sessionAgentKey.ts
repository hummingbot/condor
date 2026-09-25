/**
 * A model picked before there is a session, and whom it was picked for.
 *
 * `slug` is the chat's spelling (normalized: Condor is `""`). A bare string
 * used to stand in for this, and it forgot who the hero or panel was showing
 * when the pick was made: a model chosen on Condor's hero then rode every
 * later spawn — a specialist opened from the rail or the tab strip's `+` was
 * started on Condor's pick, and the backend, reading any non-empty key as a
 * deliberate choice, rewrote that specialist's AGENT.md model to match.
 */
export type PendingPick = { slug: string; key: string } | null;

/**
 * The pick recorded for `slug`, or `null` when there is none for that agent.
 * `slug` must already be normalized.
 */
export function pickFor(pending: PendingPick, slug: string): string | null {
  return pending && pending.slug === slug ? pending.key : null;
}

/**
 * The `agent_key` a `start_session` for `slug` carries.
 *
 * The pick, when it was made for this agent — and it is not consumed, so a
 * second chat with the same agent keeps it (clearing it would send the unbound
 * chat's stale pre-pick `defaultAgent` and persist that instead). Otherwise
 * `""` asks a bound agent for its own model, and only the unbound chat names
 * `defaultAgent`.
 */
export function sessionAgentKey(
  pending: PendingPick,
  slug: string,
  defaultAgent: string,
): string {
  return pickFor(pending, slug) ?? (slug ? "" : defaultAgent);
}
