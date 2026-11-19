/**
 * The top of the workspace rail: how many things sit beside the chat, how
 * they are arranged, and which ones.
 *
 * The tiles below it already open and close panels one by one, but nothing on
 * screen said how many could be open at once or where each would land — the
 * workspace has exactly two places beside the conversation (the wide pane and
 * the side column), and a reader only found that out by clicking. This says
 * it up front as four layouts drawn as the shape they produce — chat only,
 * chat + 1, chat + 2 side by side, chat + 2 stacked — and lets the reader pick
 * what fills each slot. It is derived from, and drives, the same state the
 * tiles do, so the two can never disagree.
 */
import { Bot, ChevronDown, Cpu, Radio, Repeat, Wallet, Zap, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";

import { isSide, type LayoutChoice, type MainSlot, type SideSlot, type SlotId } from "./layoutSlots";

export type { LayoutChoice, MainSlot, SideSlot, SlotId };

const MAIN: { id: MainSlot; label: string; Icon: LucideIcon; hint: string }[] = [
  { id: "agent", label: "Agent", Icon: Bot, hint: "What the agent knows and runs" },
  { id: "portfolio", label: "Portfolio", Icon: Wallet, hint: "Balances by asset and venue" },
  { id: "execution", label: "Bots", Icon: Cpu, hint: "What is running: deployed controllers" },
  { id: "loops", label: "Loops", Icon: Repeat, hint: "Live tick loops" },
];
const SIDE: { id: SideSlot; label: string; Icon: LucideIcon; hint: string }[] = [
  { id: "tasks", label: "Tasks", Icon: Radio, hint: "Work delegated from this chat" },
  { id: "routines", label: "Routines", Icon: Zap, hint: "Scripts this agent runs" },
];

const CHOICES: { id: LayoutChoice; label: string }[] = [
  { id: "chat", label: "Chat only" },
  { id: "one", label: "Chat + 1" },
  { id: "side", label: "Side by side" },
  { id: "stack", label: "Stacked" },
];

/**
 * The layout drawn as itself. `big` is the picker's card; the default is the
 * rail's 28 px glyph.
 */
function LayoutGlyph({ choice, big = false }: { choice: LayoutChoice; big?: boolean }) {
  const box = big ? "h-10 w-16 gap-1" : "h-4 w-7 gap-[2px]";
  const panel = "rounded-[1.5px] bg-current opacity-40";
  return (
    <span className={`flex ${box} rounded-[3px] border border-current p-[2px]`}>
      <span className="flex-[1.4] rounded-[1.5px] bg-current opacity-80" />
      {choice === "one" && <span className={`flex-1 ${panel}`} />}
      {choice === "side" && (
        <>
          <span className={`flex-1 ${panel}`} />
          <span className={`flex-[0.6] ${panel}`} />
        </>
      )}
      {choice === "stack" && (
        <span className={`flex flex-1 flex-col ${big ? "gap-1" : "gap-[2px]"}`}>
          <span className={`flex-1 ${panel}`} />
          <span className={`flex-1 ${panel}`} />
        </span>
      )}
    </span>
  );
}

export function LayoutPicker({
  choice,
  main,
  side,
  stackTop,
  stackBottom,
  onChoice,
  onMain,
  onSide,
  onStackTop,
  onStackBottom,
}: {
  /** The layout on screen now (derived from the pane, the dock and the stack flag). */
  choice: LayoutChoice;
  /** What fills the wide pane now, if anything nameable does. */
  main: MainSlot | null;
  /** What fills the side column now. */
  side: SideSlot | null;
  onChoice: (choice: LayoutChoice) => void;
  onMain: (id: MainSlot) => void;
  onSide: (id: SideSlot) => void;
  /** Split (stacked or side by side), each half takes any panel; picking the other half's swaps them. */
  stackTop: SlotId | null;
  stackBottom: SlotId | null;
  onStackTop: (id: SlotId) => void;
  onStackBottom: (id: SlotId) => void;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const current = CHOICES.find((c) => c.id === choice) ?? CHOICES[0];

  return (
    <div className="flex w-full shrink-0 flex-col items-center">
      {/* One tile: the layout on screen, drawn as itself. Choosing and editing
          happen in the menu it opens — four always-visible layout buttons took
          a third of the rail for a setting changed once in a while. */}
      <button
        ref={setAnchor}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Layout: ${current.label}. Change layout`}
        title={`Layout: ${current.label} — click to change`}
        onClick={() => setOpen((v) => !v)}
        className={`relative flex w-14 flex-col items-center gap-1 rounded-lg py-2 transition-colors ${
          open
            ? "bg-[var(--color-primary-fill)]/12 text-[var(--color-primary)]"
            : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        }`}
      >
        <LayoutGlyph choice={choice} />
        <span className="flex items-center gap-0.5 text-[10px] font-medium leading-none">
          Layout
          <ChevronDown className={`h-2.5 w-2.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>

      <AnchoredMenu anchor={anchor} open={open} onClose={() => setOpen(false)} align="right" className="w-[22rem]">
        <div className="space-y-4 p-4">
          <div>
            <p className="eyebrow">Workspace layout</p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              How many panels sit beside the chat, how they are arranged, and what goes in each.
            </p>
          </div>

          <div className="grid grid-cols-4 gap-1.5">
            {CHOICES.map((c) => {
              const active = c.id === choice;
              return (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onChoice(c.id)}
                  className={`flex flex-col items-center gap-2 rounded-lg border px-1 py-3 text-[11px] font-medium leading-tight transition-colors ${
                    active
                      ? "border-[var(--color-primary)] bg-[var(--color-primary-fill)]/8 text-[var(--color-primary)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text)]"
                  }`}
                >
                  <LayoutGlyph choice={c.id} big />
                  {c.label}
                </button>
              );
            })}
          </div>

          {choice === "stack" || choice === "side" ? (
            <>
              <SlotPicker
                title={choice === "stack" ? "Top" : "Panel 1"}
                note={choice === "stack" ? "Upper half, next to the chat" : "Next to the chat"}
                options={[...MAIN, ...SIDE]}
                value={stackTop}
                onPick={(id) => onStackTop(id as SlotId)}
              />
              <SlotPicker
                title={choice === "stack" ? "Bottom" : "Panel 2"}
                note={
                  choice === "stack"
                    ? "Lower half — drag the line between them"
                    : "On the right — drag the line between them"
                }
                options={[...MAIN, ...SIDE]}
                value={stackBottom}
                onPick={(id) => onStackBottom(id as SlotId)}
              />
            </>
          ) : (
            choice === "one" && (
              <SlotPicker
                title="Panel"
                note="Next to the chat"
                options={[...MAIN, ...SIDE]}
                value={main ?? side}
                onPick={(id) =>
                  isSide(id as SlotId) ? onSide(id as SideSlot) : onMain(id as MainSlot)
                }
              />
            )
          )}

          <p className="border-t border-[var(--color-border)] pt-3 text-[11px] leading-snug text-[var(--color-text-muted)]">
            The buttons under the layout open and close each panel directly.
          </p>
        </div>
      </AnchoredMenu>
    </div>
  );
}

function SlotPicker({
  title,
  note,
  options,
  value,
  onPick,
}: {
  title: string;
  note: string;
  options: { id: string; label: string; Icon: LucideIcon; hint: string }[];
  value: string | null;
  onPick: (id: string) => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="shrink-0 text-xs font-semibold text-[var(--color-text)]">{title}</span>
        <span className="truncate text-[11px] text-[var(--color-text-muted)]">{note}</span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        {options.map(({ id, label, Icon, hint }) => {
          const active = id === value;
          return (
            <button
              key={id}
              type="button"
              aria-pressed={active}
              title={hint}
              onClick={() => onPick(id)}
              className={`flex items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs font-medium transition-colors ${
                active
                  ? "border-[var(--color-primary)] bg-[var(--color-primary-fill)]/8 text-[var(--color-text)]"
                  : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-[var(--color-primary)]" : ""}`} />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
