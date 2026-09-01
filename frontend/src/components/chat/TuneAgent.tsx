import { SlidersHorizontal } from "lucide-react";

/**
 * The way into what the agent answering this conversation is made of.
 *
 * One button, at the right end of the bar the session tabs already sit in:
 * the tabs say who is answering, and this says what you can do about it. It
 * spent a release as a card at the top of the context dock, on the theory that
 * every door in the workspace should be on the same side of the window — but
 * the dock is where a conversation's *work* is listed, and the agent is not
 * work. Reaching across the window to change what you are talking to, from a
 * column about tasks and routines, never became the obvious gesture it was
 * supposed to be.
 *
 * The top bar is the chrome that belongs to the conversation as a whole, which
 * is exactly the scope of what the panel changes. A control there is also
 * findable from the hero, before any session exists and before the dock has
 * anything to list.
 *
 * The label carries the verb and nothing else. Who that is is on the tab an
 * inch to the left, and the panel it opens is titled with the name again —
 * a third statement in the same row would be the thing FEAT-081 spent a commit
 * removing. The tooltip has it for the reader who wants confirmation before
 * clicking.
 *
 * Everything the panel holds — what the agent is for, what it runs on, and
 * everything it knows — stays one click away in {@link AgentPanel}.
 */
export function TuneAgentButton({
  name,
  open,
  onOpen,
}: {
  /** Who is on the other end — the bound Agent, or Condor. */
  name: string;
  /** Whether the agent's panel is the one in the pane. */
  open: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      onClick={onOpen}
      aria-pressed={open}
      title={`Tune ${name} — read and change what this agent is`}
      className={`flex shrink-0 items-center gap-1.5 rounded px-1.5 py-1 text-xs font-medium transition-colors hover:bg-[var(--color-surface-hover)] ${
        open
          ? "bg-[var(--color-surface-hover)] text-[var(--color-text)]"
          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
    >
      <SlidersHorizontal
        className={`h-3.5 w-3.5 shrink-0 ${open ? "text-[var(--color-accent)]" : ""}`}
      />
      Tune agent
    </button>
  );
}
