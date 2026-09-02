import { Cpu, Wallet } from "lucide-react";
import { useState } from "react";

import { DockExecution } from "@/components/chat/DockExecution";
import { DockPortfolio } from "@/components/chat/DockPortfolio";
import { DockSection } from "@/components/chat/DockSection";
import { ACCOUNT_DOCK_KEY } from "@/lib/sessionState";

/** The two questions a person holds in their head while typing at a trader. */
type PanelId = "portfolio" | "execution";

const PANELS: {
  id: PanelId;
  label: string;
  Icon: typeof Wallet;
  /** What the tab opens, said in terms of the server it is about. */
  hint: (server: string) => string;
  /** Why the tab is dead when there is no server to ask about. */
  disabledHint: string;
}[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    Icon: Wallet,
    hint: (server) => `Portfolio on ${server}`,
    disabledHint: "Select a server to see your portfolio",
  },
  {
    id: "execution",
    label: "Execution",
    Icon: Cpu,
    hint: (server) => `Execution on ${server}`,
    disabledHint: "Select a server to see what is running",
  },
];

function readOpen(): PanelId[] {
  try {
    const raw = JSON.parse(localStorage.getItem(ACCOUNT_DOCK_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return PANELS.map((p) => p.id).filter((id) => raw.includes(id));
  } catch {
    // Unreadable storage is a browser that has never opened a panel, not an
    // error to surface: the rail is how they come back either way.
    return [];
  }
}

/**
 * The desk you trade, beside the conversation you are having about it.
 *
 * `ContextDock`'s subject is *this conversation* — the tasks it started, the
 * routines the agent owns. These two panels are about the **server**, which is
 * a different subject and does not belong under that header; each one names the
 * server in its own, so the two docks can never be read for each other's
 * numbers.
 *
 * It is a rail plus an overlay rather than a column, and that is the whole
 * design (FEAT-094). The workspace row is already four things wide with two
 * load-bearing floors in it — the transcript's `xl:min-w-[360px]` and the
 * context dock's `MIN_CHAT_PX` — and a fifth *column* would be spent out of
 * those. 40px of permanent chrome costs neither, and the panel floats at `z-40`
 * over a dock whose own overlay is `z-30`, so opening one never moves the
 * transcript by a pixel.
 *
 * Accepted deliberately: an open panel covers Tasks and Routines. That is the
 * glance model — open it, read it, put it away — and it is exactly what buys
 * the transcript its width back. Both full pages are one click away in the
 * panel's own footer.
 *
 * **Closed costs nothing.** With no panel open there is no column at all, so
 * neither panel's queries nor its socket channels are ever mounted: the Agents
 * page is exactly as expensive as it was for anyone who never opens a tab. That
 * is why a closed `DockSection` unmounts its body rather than hiding it.
 */
export function AccountDock({ server }: { server: string | null }) {
  const [open, setOpen] = useState<PanelId[]>(readOpen);

  const toggle = (id: PanelId) => {
    setOpen((prev) => {
      const next = prev.includes(id)
        ? prev.filter((p) => p !== id)
        : [...prev, id];
      localStorage.setItem(ACCOUNT_DOCK_KEY, JSON.stringify(next));
      return next;
    });
  };

  // A panel is only reachable with a server to ask about: a disclosure that
  // opens onto a column of zeroes is worse than a control that says why it is
  // dead. Anything left open from a previous server stays recorded and comes
  // back when one is selected again.
  const shown = server ? open : [];

  return (
    <>
      {shown.length > 0 && (
        /* Outboard of the context dock and over it: `right-10` clears the rail
           beside it, and `z-40` is above the dock's own `z-30` overlay and
           below the `z-50` that sheets and menus own. */
        <div className="absolute inset-y-0 right-10 z-40 flex w-[320px] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl">
          {PANELS.map(({ id, label, Icon, hint }) => (
            <DockSection
              key={id}
              icon={<Icon className="h-3 w-3 shrink-0" />}
              /* The server is in the label, not only in the tooltip: a total
                 with no desk attached to it is a number nobody can act on. */
              label={`${label} · ${server}`}
              hint={hint(server!)}
              open={shown.includes(id)}
              onToggle={() => toggle(id)}
            >
              {id === "portfolio" ? (
                <DockPortfolio server={server!} />
              ) : (
                <DockExecution server={server!} />
              )}
            </DockSection>
          ))}
        </div>
      )}

      <aside className="flex w-10 shrink-0 flex-col items-center gap-3 border-l border-[var(--color-border)] bg-[var(--color-bg)] py-2">
        {PANELS.map(({ id, label, Icon, hint, disabledHint }) => {
          const isOpen = shown.includes(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => toggle(id)}
              disabled={!server}
              aria-pressed={isOpen}
              aria-label={label}
              title={server ? hint(server) : disabledHint}
              className={`rounded p-1 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                isOpen
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-muted)]"
              }`}
            >
              <Icon className="h-4 w-4" />
            </button>
          );
        })}
      </aside>
    </>
  );
}
