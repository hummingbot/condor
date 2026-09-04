// ── Charts that outlive the tree they were drawn in (PERF-328) ──
//
// A streaming answer renders as a list of frozen chunks plus a live tail
// (PERF-327); a settled one renders as one whole-text pass, because that is
// the render a reloaded transcript goes through and what the reader is left
// looking at must not depend on whether they watched it arrive. Both of those
// are right, and together they mean the answer's DOM is replaced once, when the
// turn settles. For prose that is invisible. For a chart it is a teardown: the
// container re-measures, the series re-animate, and the one part of the answer
// worth looking at blinks at the moment the answer is finished.
//
// React cannot be asked to reconcile across that: the two trees share no
// position, and a key only settles identity between siblings of one parent. So
// the chart is not in either tree. It is rendered through a portal from here —
// a component that sits *above* the swap and is never unmounted by it — into a
// plain <div> this module owns. The markdown tree contributes only the place
// that div is parked: a slot that adopts it on mount and lets go on unmount,
// which is a DOM move inside a single commit, before the browser paints.
//
// Identity comes from the text (`chartFences`), the only thing the two passes
// have in common: the nth chart fence in the answer keeps the nth container.
// Where the text cannot single one out — two fences with identical bodies —
// the slot renders the chart inline, exactly as it did before this existed.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { chartFences } from "@/lib/chartFences";
import { ChartBlock } from "./ChartBlock";

/** Where a chart's instance lives, looked up by the fence body it was drawn
 *  from. `null` when the answer's fences cannot name one unambiguously. */
type SlotRegistry = (raw: string) => HTMLDivElement | null;

const SlotContext = createContext<SlotRegistry | null>(null);

/**
 * The owner of every chart in one answer.
 *
 * It renders `children` — the markdown, whichever of the two passes is current
 * — plus one portal per chart fence. The portals are the charts; nothing about
 * them changes when the markdown underneath is swapped, because this component
 * is not swapped.
 */
export function ChartSlotHost({
  text,
  live,
  children,
}: {
  text: string;
  live: boolean;
  children: ReactNode;
}) {
  const fences = useMemo(() => chartFences(text), [text]);

  // An answer only ever grows, so the nth container is the nth container for
  // the rest of the turn. It is held in state rather than a ref because it is
  // read while rendering — and grown there too, which is safe because growing
  // it is idempotent: a render React throws away leaves behind at worst an
  // unused <div>. It cannot be created in the slot, which is the one thing
  // here that does not outlive the swap.
  const [containers] = useState<HTMLDivElement[]>(() => []);
  while (containers.length < fences.length) {
    containers.push(document.createElement("div"));
  }

  // Two fences with identical bodies are indistinguishable from inside the
  // markdown, so neither is adopted: both render where they stand, which is
  // what they did before any of this existed.
  const owned = useMemo(() => {
    const byBody = new Map<string, number>();
    fences.forEach((body, i) => byBody.set(body, byBody.has(body) ? -1 : i));
    return byBody;
  }, [fences]);

  const registry = useMemo<SlotRegistry>(
    () => (raw: string) => {
      const at = owned.get(raw.trim());
      return at === undefined || at === -1 ? null : containers[at];
    },
    [owned, containers],
  );

  return (
    <SlotContext.Provider value={registry}>
      {children}
      {[...owned].map(([body, i]) =>
        i === -1
          ? null
          : createPortal(
              <ChartBlock raw={body} live={live} />,
              containers[i],
              String(i),
            ),
      )}
    </SlotContext.Provider>
  );
}

/**
 * A ```chart fence's place in the markdown.
 *
 * The chart itself belongs to the host above; this is the parking space for
 * it. When the answer settles, this element is destroyed and an identical one
 * is created in the other tree — and all that happens to the chart is that it
 * is appended to the new one, in the same commit, without a repaint between.
 *
 * With no host to ask — a system note, a shared transcript — this is just the
 * chart, rendered where it stands.
 */
export function ChartSlot({ raw, live }: { raw: string; live: boolean }) {
  const registry = useContext(SlotContext);
  const container = registry ? registry(raw) : null;

  const park = useCallback(
    (node: HTMLDivElement | null) => {
      if (node && container && container.parentNode !== node) {
        node.appendChild(container);
      }
    },
    [container],
  );

  if (!container) return <ChartBlock raw={raw} live={live} />;
  return <div ref={park} />;
}
