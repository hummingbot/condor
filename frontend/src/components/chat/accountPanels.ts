/**
 * Which desk panels exist, which are open, and how they reach the rail.
 *
 * Split from the panel that draws them because the two halves of this feature
 * live in different places: the sections are drawn in the workspace pane
 * (`AccountDock`), and the words that open them belong to one strip shared with
 * everything else on the right edge (`WorkspaceRail`). The page composes both,
 * so the state they share cannot sit inside either.
 *
 * ## The desk is the pane's, not a column of its own
 *
 * It shipped as a fourth resizable column, inboard of the context dock, and the
 * row could not pay for it: the agent panel, the desk and the two docks all
 * want a real width at once, and the three surfaces a reader actually *works*
 * in — the agent, the portfolio, the execution table — are the same three that
 * need the room. So the desk lives in the workspace pane now, where the agent
 * panel already lived. Opening the agent puts the desk away and opening the
 * desk puts the agent away, which is what the pane already does to two sheets
 * and no longer has to be a rule anybody enforces. Tasks and Routines keep
 * their narrow column: those are watched out of the corner of an eye while you
 * type, not worked in.
 *
 * Which is why `open` comes in from the workspace rather than living here. This
 * hook owns one fact — *which sections* the reader wants — and the pane owns
 * the other: whether the desk is the thing on screen.
 */
import { Cpu, Wallet } from "lucide-react";
import { useState } from "react";

import type { RailItem } from "@/components/chat/WorkspaceRail";
import { ACCOUNT_DOCK_KEY } from "@/lib/sessionState";

/** The two questions a person holds in their head while typing at a trader. */
export type PanelId = "portfolio" | "execution";

export const PANELS: {
  id: PanelId;
  label: string;
  /** The section header's glyph, carried onto the rail tile beside it. */
  Icon: typeof Wallet;
  /** What the section is, for the reader telling it from the one below. */
  hint: string;
  /** Why the tab is dead when there is no server to ask about. */
  disabledHint: string;
}[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    Icon: Wallet,
    hint: "What you hold, by asset and by venue",
    disabledHint: "Select a server to see your portfolio",
  },
  {
    id: "execution",
    label: "Execution",
    Icon: Cpu,
    hint: "The controllers trading right now, and what each has done",
    disabledHint: "Select a server to see what is running",
  },
];

/** In the order the panel draws them, whatever order they were clicked in. */
function ordered(ids: unknown[]): PanelId[] {
  return PANELS.map((p) => p.id).filter((id) => ids.includes(id));
}

function readOpen(): PanelId[] {
  try {
    const raw = JSON.parse(localStorage.getItem(ACCOUNT_DOCK_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return ordered(raw);
  } catch {
    // Unreadable storage is a browser that has never opened a panel, not an
    // error to surface: the rail is how they come back either way.
    return [];
  }
}

/**
 * Was the desk up when this browser was last here?
 *
 * The workspace asks before it decides what is in the pane, because the pane
 * is its state and this is the only surface with a memory. The recorded
 * sections *are* the answer: a desk with no section open is not a desk.
 */
export function deskWasOpen(): boolean {
  return readOpen().length > 0;
}

/**
 * Which desk sections are open, and the rail entries that open them.
 *
 * The reader's picks are written down here, so a reload comes back to the same
 * desk — and so does the agent panel taking the pane and giving it back, which
 * is the whole reason the sections are remembered separately from whether the
 * panel is up.
 */
export function useAccountPanels({
  server,
  open,
  onOpenChange,
}: {
  server: string | null;
  /** The desk is what the workspace pane is showing right now. */
  open: boolean;
  /** Ask the workspace for the pane, or give it back. */
  onOpenChange: (open: boolean) => void;
}) {
  const [sections, setSections] = useState<PanelId[]>(readOpen);

  const write = (next: PanelId[]) => {
    setSections(next);
    localStorage.setItem(ACCOUNT_DOCK_KEY, JSON.stringify(next));
  };

  /**
   * Turn a section on or off — and, with it, the panel.
   *
   * With the panel away a tile can only mean "show me this": it reads
   * unpressed, because nothing is on screen to be pressed *about*, and a click
   * that quietly turned a section off while opening the desk on the other one
   * would be a control doing the opposite of what it looks like. So a click
   * from closed adds, and brings back whatever else was open when the desk was
   * last put away — the desk you left, not a fresh one.
   *
   * Turning the last section off closes the panel rather than leaving an empty
   * one with two collapsed headers in it — and, since the sections are the
   * record, that is a close that sticks across a reload.
   */
  const toggle = (id: PanelId) => {
    if (!open) {
      write(sections.includes(id) ? sections : ordered([...sections, id]));
      onOpenChange(true);
      return;
    }
    const next = sections.includes(id)
      ? sections.filter((p) => p !== id)
      : ordered([...sections, id]);
    write(next);
    if (next.length === 0) onOpenChange(false);
  };

  /**
   * Put the desk away.
   *
   * This forgets the sections, where losing the pane to the agent panel does
   * not: the two look alike on screen and are not the same act. Closing from
   * the panel's own bar is the reader saying they are done with the desk, and
   * it is the only fact that survives a reload — `sections` being non-empty is
   * what brings the panel back on the next mount ({@link deskWasOpen}), so a
   * close that kept them would be a close that undid itself.
   */
  const close = () => {
    write([]);
    onOpenChange(false);
  };

  // A section is only reachable with a server to ask about: a disclosure that
  // opens onto a column of zeroes is worse than a control that says why it is
  // dead. Anything left open from a previous server stays recorded and comes
  // back when one is selected again.
  const shown = server && open ? sections : [];

  const railItems: RailItem[] = PANELS.map(
    ({ id, label, Icon, hint, disabledHint }) => ({
      id,
      label,
      // The same glyph the section header inside the panel carries, so the tile
      // and the thing it opens are recognisably one control.
      Icon,
      hint: server ? `${hint} · ${server}` : hint,
      active: shown.includes(id),
      disabled: !server,
      disabledHint,
      onToggle: () => toggle(id),
    }),
  );

  return { shown, toggle, close, railItems };
}
