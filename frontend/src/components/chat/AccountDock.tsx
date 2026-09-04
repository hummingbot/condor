import { DockExecution } from "@/components/chat/DockExecution";
import { DockPortfolio } from "@/components/chat/DockPortfolio";
import { DockSection } from "@/components/chat/DockSection";
import { PANELS, type PanelId } from "@/components/chat/accountPanels";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";

/**
 * The desk you trade, beside the conversation you are having about it.
 *
 * `ContextDock`'s subject is *this conversation* — the tasks it started, the
 * routines the agent owns. These two sections are about the **server**, which
 * is a different subject and does not belong under that header; this panel
 * names the server in its own bar, so the two can never be read for each
 * other's numbers.
 *
 * ## Where it sits (FEAT-094, revised twice)
 *
 * In the **workspace pane** — the same column the agent panel opens in, at the
 * same even split, closing with the same control. It has been three things
 * now, and the road there is the argument for where it ended up:
 *
 * 1. A panel floating at `z-40` over the context dock. It covered Tasks and
 *    Routines exactly, so the two docks were mutually exclusive in practice:
 *    you could watch a delegation or watch your balance, never both.
 * 2. A resizable column of its own, in flow, inboard of that dock. Two
 *    questions asked at once now got two columns — and the row got a fourth
 *    and a fifth. Chat, agent panel, desk, dock and rail all want a real width
 *    simultaneously, and the sum of their floors is wider than a laptop.
 * 3. The pane. Because the three surfaces that need the room — the agent, the
 *    portfolio, the execution table — are also the three you never read *at
 *    the same time*: you tune an agent, or you look at what you hold, or you
 *    look at what is trading. One at a time in one wide column beats three at
 *    once in three narrow ones, and it is one column of chrome to learn rather
 *    than three.
 *
 * Portfolio and Execution are the exception to "one at a time", and that is
 * deliberate: they are the same subject asked twice — what you hold and what
 * is moving it — so they share the panel as two panes, exactly as Tasks and
 * Routines share the dock. Opening the agent puts both away, because the pane
 * has one occupant and the union in `AgentChatTab` says so.
 *
 * **Closed still costs nothing.** With no section open there is no panel at
 * all, so neither section's queries nor its socket channels are ever mounted:
 * the Agents page is exactly as expensive as it was for anyone who never opens
 * a tab. That is why a closed `DockSection` unmounts its body rather than
 * hiding it, and it is the one part of the original design that neither move
 * touched.
 */
export function AccountDock({
  server,
  shown,
  onToggle,
  onClose,
  onOpenAgent,
}: {
  server: string | null;
  /** The open sections, from {@link useAccountPanels}. */
  shown: PanelId[];
  onToggle: (id: PanelId) => void;
  onClose: () => void;
  /**
   * Open an agent's panel in the pane (FEAT-114) — handed straight to the
   * execution section, whose agent rows are the only thing here that names one.
   */
  onOpenAgent?: (slug: string) => void;
}) {
  if (shown.length === 0 || !server) return null;

  return (
    <WorkspaceSheet
      // The server, once, at the top — a total with no desk attached to it is
      // a number nobody can act on, and saying it on every section header
      // spent the width the tables now use.
      title={server}
      subtitle="Portfolio and execution"
      onClose={onClose}
      // No full screen, for the reason the agent panel has none: this is read
      // against the conversation beside it, and the only outcome of the
      // gesture is losing the chat that made you open it.
      fullscreen={false}
      // A workbench, not a report: an even split, and its own remembered drag.
      paneProfile="tune"
      // The sections own their scrolling — each is a pane with a fixed share of
      // the panel, so a long portfolio can never push the execution header off
      // the bottom.
      bleed
    >
      <div
        data-testid="account-dock"
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        {PANELS.map(({ id, label, Icon, hint }) => (
          <DockSection
            key={id}
            icon={<Icon className="h-3 w-3 shrink-0" />}
            label={label}
            hint={hint}
            open={shown.includes(id)}
            onToggle={() => onToggle(id)}
          >
            {id === "portfolio" ? (
              <DockPortfolio server={server} />
            ) : (
              <DockExecution server={server} onOpenAgent={onOpenAgent} />
            )}
          </DockSection>
        ))}
      </div>
    </WorkspaceSheet>
  );
}
