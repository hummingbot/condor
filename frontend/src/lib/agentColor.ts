import type { ChatMessage } from "@/hooks/useChatSocket";

/**
 * Who is speaking in a transcript, and in what colour.
 *
 * The chat can hold more than one counterpart — a handover mid-conversation is
 * a supported move — and until now every answer was drawn with the same grey
 * `Bot` glyph, so a two-agent transcript was not scannable at all. The agent's
 * name and a gutter in its own colour is what replaces that.
 */

/**
 * The four colours an agent can be given.
 *
 * `--chart-series-1..4` and nothing else: they are the only palette in this app
 * that has been validated for CVD separation, lightness band and contrast
 * (see index.css), and the rule recorded beside them — never reordered, never
 * extended by generating a hue — holds for identity exactly as it does for a
 * series. Four is enough for what the gutter is for: telling this turn's
 * speaker from the one above it.
 */
export const AGENT_COLOR_VARS = [
  "--chart-series-1",
  "--chart-series-2",
  "--chart-series-3",
  "--chart-series-4",
] as const;

/**
 * The custom property standing for `id`, stable for the life of the name.
 *
 * FNV-1a rather than a sum of char codes: the ids in play are short and share
 * prefixes ("condor", "backpack_mm", "brigado"), and a sum collides those into
 * neighbouring buckets. Pure — the same agent is the same colour in every
 * session, on every machine, which is the only reason a colour can be learned.
 */
export function agentColorVar(id: string): (typeof AGENT_COLOR_VARS)[number] {
  let hash = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return AGENT_COLOR_VARS[Math.abs(hash) % AGENT_COLOR_VARS.length];
}

/** Same, ready to drop into a `style` prop. */
export function agentColor(id: string): string {
  return `var(${agentColorVar(id)})`;
}

/** The divider a brain switch writes into the scrollback, as it is worded in
 *  `useChatSocket.switchBrain`. Parsed rather than carried on the message
 *  because that line is also what the backend records, so a reloaded
 *  transcript says who took over exactly like a live one does. */
const SWITCHED_TO = /^Switched to (.+)$/;

/**
 * The speaker of each message, in order.
 *
 * A turn that says who took it is believed: `agentSlug` is stamped by the
 * backend (and, live, at the moment the bubble opens), so it survives a
 * handover that the conversation's binding does not. That matters because
 * `initial` is the binding *now* — after a handover it names the agent that
 * took over, so seeding the whole transcript from it credited every earlier
 * answer to the newcomer and collapsed a two-counterpart chat to one name and
 * one gutter colour.
 *
 * The forward walk from `initial` is the fallback, for the turns that carry no
 * attribution: a user line, a divider, and any assistant turn recorded before
 * the backend stamped one. It walks forwards because a handover names who is
 * taking over and not who is being taken over from.
 *
 * `resolve` turns a stamped slug into a display name — `""` is the default
 * agent, an unknown slug is its own best name. Without one the caller has no
 * roster to resolve against, so a default-agent turn falls back to `initial`.
 */
export function speakerNames(
  messages: ChatMessage[],
  initial: string,
  resolve: (slug: string) => string = (slug) => slug || initial,
): string[] {
  let speaker = initial;
  return messages.map((msg) => {
    if (msg.role === "system" && msg.kind === "switch") {
      const match = SWITCHED_TO.exec(msg.text);
      if (match) speaker = match[1];
    }
    return msg.agentSlug === undefined ? speaker : resolve(msg.agentSlug);
  });
}
