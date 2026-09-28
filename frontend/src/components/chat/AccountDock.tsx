import { Fragment } from "react";

import { DockExecution } from "@/components/chat/DockExecution";
import { DockLoops } from "@/components/chat/DockLoops";
import { DockPortfolio } from "@/components/chat/DockPortfolio";
import { DockSection } from "@/components/chat/DockSection";
import { DockSplitHandle } from "@/components/chat/DockSplitHandle";
import { PANELS, type PanelId } from "@/components/chat/accountPanels";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { useDockSplit } from "@/hooks/useDockSplit";
import { DESK_SPLIT_KEY } from "@/lib/sessionState";

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
 * How the two split the panel is the reader's, dragged on the seam between them
 * and remembered (see `DockSplit`): a portfolio of five assets and a fleet of
 * forty controllers do not want the same half, and collapsing one away was the
 * only answer the even split had for that.
 *
 * **Closed still costs nothing.** With no section open there is no panel at
 * all, so neither section's queries nor its socket channels are ever mounted:
 * the Agents page is exactly as expensive as it was for anyone who never opens
 * a tab. That is why a closed `DockSection` unmounts its body rather than
 * hiding it, and it is the one part of the original design that neither move
 * touched.
 *
 * **Loops joined as a third section (FEAT-1xx), not a fourth panel.** It used
 * to be its own sheet — the rail's global "every agent, every server" view,
 * `LoopsPanel` — which put a reader's own server's loops behind a second
 * overlay after they had already opened this one, at a width neither of them
 * chose. `DockLoops` reads the identical cards (`LoopCardGrid`) narrowed to
 * this panel's own server (`loopsOnServer`, the same rule `DockExecution`'s
 * agent rows already follow), so the three sections finally answer one
 * question each at one width instead of two surfaces disagreeing about how
 * wide a loop card should be.
 */
export function AccountDock({
  server,
  shown,
  onToggle,
  onClose,
  onOpenAgent,
  onOpenLoop,
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
  /**
   * Open a loop's strategy sheet — handed straight to the Loops section, the
   * same hand-off `LoopsPanel`'s own cards make (FEAT-1xx).
   */
  onOpenLoop?: (agentSlug: string, strategySlug: string) => void;
}) {
  const { frac, setFrac, defaultFrac } = useDockSplit(DESK_SPLIT_KEY);
  if (shown.length === 0 || !server) return null;

  // The seam only knows how to divide *two* panes — one dragged fraction and
  // its complement — and only the original two: Portfolio and Execution are
  // always adjacent in `PANELS`, so the handle's own geometry read
  // (`previousElementSibling`/`nextElementSibling`) lands on the two `<div>`s
  // either side of it. Loops sits third, so any pairing that includes it, and
  // the three-way case, fall back to an even split instead — `DockSection`'s
  // own default when `share` is omitted — rather than a drag model whose
  // sibling read would land on the wrong pane the moment a closed section
  // sits between the two open ones.
  const split = shown.length === 2 && shown.includes("portfolio") && shown.includes("execution");

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
          <Fragment key={id}>
            {/* Only the Portfolio/Execution pair drags — see `split` above —
                and only Execution ever needs the handle above it, since
                Portfolio is always first. */}
            {split && id === "execution" && (
              <DockSplitHandle
                frac={frac}
                setFrac={setFrac}
                defaultFrac={defaultFrac}
                label="Resize portfolio and execution"
              />
            )}
            <DockSection
              icon={<Icon className="h-3 w-3 shrink-0" />}
              label={label}
              hint={hint}
              open={shown.includes(id)}
              // The dragged split only ever applies to the Portfolio/Execution
              // pair; every other combination — Loops alone or beside either
              // one, or all three together — shares evenly, `DockSection`'s
              // own default when `share` is omitted.
              share={
                split ? (id === "portfolio" ? frac : 1 - frac) : undefined
              }
              onToggle={() => onToggle(id)}
            >
              {id === "portfolio" ? (
                <DockPortfolio server={server} />
              ) : id === "execution" ? (
                <DockExecution server={server} onOpenAgent={onOpenAgent} />
              ) : (
                <DockLoops
                  server={server}
                  onOpenLoop={onOpenLoop ?? (() => {})}
                />
              )}
            </DockSection>
          </Fragment>
        ))}
      </div>
    </WorkspaceSheet>
  );
}
