/**
 * The right edge of the workspace: everything that opens beside the chat.
 *
 * One strip, one vocabulary. Before this the edge grew a rail per owner — the
 * account dock's entries, the context dock's own strip when it was collapsed —
 * and the way into the agent lived somewhere else entirely, a button in the
 * conversation's top bar. Three places to look for "what else can I open here",
 * and, once two of them were rails, two identical 40 px strips side by side
 * separated by a border that explained nothing. A reader cannot tell a seam
 * that means "different subject" from one that means "different component",
 * so the two rails are now one and the subjects are said out loud.
 *
 * ## A tile, not a spine
 *
 * The labels used to be set upright and stacked, one letter per line, reading
 * down the strip — words instead of glyphs, which was the right instinct and
 * the wrong shape. A 9-letter word at 10 px with the tracking upright text
 * needs runs ~130 px down the edge; five of them is ~500 px of travel for five
 * clicks, and every one of them has to be assembled letter by letter before it
 * is a word. So each entry is a tile instead: the icon its own dock section
 * already uses, the word underneath it set horizontally, and the whole thing
 * inside a rounded hit target you can see. Five entries now run ~240 px.
 *
 * The icon is not decoration and not a replacement for the word — it is the
 * *same* glyph that heads the panel the tile opens, so the rail and the panel
 * are recognisably one thing. A rail of glyphs alone is a rail nobody can read
 * without hovering; a rail of words alone throws away the fastest half of the
 * recognition. Both, at 64 px, costs less width than the two 40 px strips it
 * replaces.
 *
 * ## Groups
 *
 * Three subjects, and they are not interchangeable: the agent answering, the
 * **server** you are trading (a balance, the controllers running), and **this
 * conversation** (what it delegated, what it ran). They are separated by a
 * rule and nothing else.
 *
 * They used to be captioned — DESK over Portfolio and Execution, CHAT over
 * Tasks and Routines — on the theory that a reader had to be told which
 * selector each group follows. In a 64 px strip that theory cost more than it
 * bought: two more words to read on the way to a click, set at 8 px in the
 * muted colour, in a column whose entries are already single words. And they
 * were the wrong words anyway — "Chat" captioned two tiles sitting on the edge
 * of a chat, which names the whole screen rather than the group. A rule says
 * "these are not those", which is the only thing the caption was really for.
 *
 * The strip scrolls if it ever has to, and every group refuses to shrink: a
 * flex column whose children may compress does not overflow, it flattens, and
 * a flattened tile is one whose icon and word are clipped by the tile above.
 * That is what a short window did to the lead entry — the agent's, the one
 * that had to stay reachable. A short window is the one place where a rail
 * that clips is worse than a rail that scrolls.
 */
import type { LucideIcon } from "lucide-react";

/** One thing the rail can open, and whether it is open. */
export type RailItem = {
  id: string;
  /** The word under the icon. Kept to one — this is a tab, not a sentence. */
  label: string;
  /** The panel's own glyph, so the tile and what it opens read as one thing. */
  Icon?: LucideIcon;
  /** What it opens, for the tooltip. */
  hint: string;
  /** The panel this button opens is the one on screen. */
  active: boolean;
  /** Nothing to open — a server has to be selected first, say. */
  disabled?: boolean;
  /** Why it is dead, in place of the hint. */
  disabledHint?: string;
  /** Something is running in there right now. */
  count?: number;
  onToggle: () => void;
};

/** Entries that answer the same question, ruled off from the ones that do not. */
export type RailGroup = {
  /** Not drawn — a stable key, and what the group is, for whoever reads this. */
  id: string;
  items: RailItem[];
};

export function WorkspaceRail({ groups }: { groups: RailGroup[] }) {
  return (
    <aside
      data-testid="workspace-rail"
      className="flex w-16 shrink-0 flex-col items-center gap-0.5 overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-bg)] py-2"
    >
      {groups
        .filter((g) => g.items.length > 0)
        .map((group, i) => (
          <div
            key={group.id}
            // `shrink-0`, or a window too short for the strip compresses the
            // groups instead of scrolling them and the tiles are clipped.
            className={`flex w-full shrink-0 flex-col items-center gap-0.5 ${
              // The rule belongs to the group below it, so the first one — the
              // top of the strip — never draws a line against the bar above.
              i > 0 ? "mt-1.5 border-t border-[var(--color-border)] pt-1.5" : ""
            }`}
          >
            {group.items.map((item) => (
              <RailButton key={item.id} {...item} />
            ))}
          </div>
        ))}
    </aside>
  );
}

/**
 * One tile on the rail.
 *
 * Exported so a test — and anything that ever has to place a single entry
 * outside the strip — gets the identical control rather than an approximation
 * of it. There is only one rail now; there must still only be one button.
 */
export function RailButton({
  label,
  Icon,
  hint,
  active,
  disabled,
  disabledHint,
  count,
  onToggle,
}: Omit<RailItem, "id">) {
  const running = count !== undefined && count > 0;
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={active}
      aria-label={label}
      title={disabled ? disabledHint || hint : hint}
      className={`relative flex w-14 shrink-0 flex-col items-center gap-1 rounded-lg px-1 py-2 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "bg-[var(--color-primary)]/12 text-[var(--color-primary)]"
          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-muted)]"
      }`}
    >
      {/* The open panel, marked on the edge nearest it rather than by tint
          alone: a 12 % wash is legible next to its neighbours and invisible on
          its own, which is the state a reader coming back to the tab is in. */}
      {active && (
        <span
          aria-hidden
          className="absolute inset-y-1.5 right-0 w-0.5 rounded-full bg-[var(--color-primary)]"
        />
      )}
      <span className="relative">
        {Icon && <Icon className="h-4 w-4" />}
        {/* On the icon, not under the word: a bare number below a label reads
            as a second label, and the thing that is running is the panel the
            glyph stands for. */}
        {running && (
          <span className="absolute -right-2.5 -top-1.5 flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-emerald-500 px-1 text-[8px] font-bold leading-none text-white">
            {count}
          </span>
        )}
      </span>
      {/* `leading-tight`, not `leading-none`: `truncate` hides the overflow of
          this box, and a line box exactly as tall as the font size clips off
          whatever hangs below the baseline — the "g" of Agent, in practice. */}
      <span className="w-full truncate text-center text-[9px] font-medium leading-tight tracking-wide">
        {label}
      </span>
    </button>
  );
}
