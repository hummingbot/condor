import { Bot, ChevronRight } from "lucide-react";

/**
 * Who is answering this conversation, at the top of the column that already
 * says what the conversation is doing.
 *
 * One line: the agent's name, and the chevron that says it opens. Nothing
 * else — not the description, not the model, not the server. Those were here
 * once, and between them, the session tab and the header chip beside it, the
 * same agent was named three times on one screen and its wiring twice. A dock
 * card that restates what is already on screen is not a summary, it is noise
 * with a border around it.
 *
 * So this is the *only* place the agent is named as an agent, and it is the
 * door: everything it used to say — what it is for, what it runs on, where it
 * trades, and everything it knows — is one click away in {@link AgentPanel},
 * which is where a reader who wants any of it is going anyway.
 *
 * It sits with Tasks and Routines rather than inside the panel it opens, so
 * every door in this workspace is on the same side of the window: you click a
 * routine here and its report opens in the pane to the left, and you click the
 * agent here and the agent opens the same way.
 */
export function DockAgentCard({
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
    <div className="shrink-0 border-b border-[var(--color-border)] px-2.5 py-1.5">
      {/* The whole identity is the door: the name of the agent is also the way
          to read it, the way a routine's row is the way to read its report. */}
      <button
        onClick={onOpen}
        aria-pressed={open}
        title={`${name} — read and change what this agent is`}
        className={`group flex w-full items-center gap-1.5 rounded px-1 py-1 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
          open ? "bg-[var(--color-surface-hover)]" : ""
        }`}
      >
        <Bot className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent)]" />
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-[var(--color-text)]">
          {name}
        </span>
        <ChevronRight
          className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
      </button>
    </div>
  );
}
