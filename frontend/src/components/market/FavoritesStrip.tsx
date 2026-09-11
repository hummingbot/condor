import { Fragment, useRef, useState } from "react";
import { Star, X } from "lucide-react";

import { formatConnectorName } from "@/lib/formatters";
import type { FavoriteMarket } from "@/lib/marketFavorites";
import { useMarketFavorites } from "@/lib/marketFavorites";
import type { MarketPick } from "./MarketBrowser";

interface FavoritesStripProps {
  /** Whose stars these are: a favourite belongs to one server, never to all. */
  server: string;
  /** The venue the trade surface is on, so an off-venue chip can say so. */
  connector: string;
  /** The pair on screen, highlighted in the strip. */
  pair: string;
  onPick: (market: MarketPick) => void;
}

/** A chip as rendered, carrying the index of the star it came from. */
interface Chip {
  fav: FavoriteMarket;
  /** Index into `favorites` — the only index `reorder` will accept. */
  index: number;
}

/**
 * The chips in render order: this venue first, then the rest.
 *
 * Grouping is derived from the venue on screen, never stored, so the user's
 * drag order survives inside each group and a venue change re-groups without
 * writing anything. Each chip keeps the index it had in `favorites`, because
 * that — not the slot it happens to render in — is what `reorder` moves.
 */
function group(favorites: FavoriteMarket[], connector: string): Chip[] {
  const here: Chip[] = [];
  const elsewhere: Chip[] = [];
  favorites.forEach((fav, index) => {
    (fav.connector === connector ? here : elsewhere).push({ fav, index });
  });
  return [...here, ...elsewhere];
}

/**
 * The pairs starred on this server, one click from the trade surface.
 *
 * Before this, a star only paid off inside the market browser — you opened the
 * overlay, read the starred rows off the top, picked one, and the overlay went
 * away. That is three actions to reach a pair you look at every day. The strip
 * is the same set of stars rendered where you are already looking.
 *
 * Navigation, not a ticker: the chips carry no price or change. Live numbers
 * for a dozen pairs across venues is a second market-data subscription for a
 * control whose whole job is "put me on that chart", and the price the user
 * came for is already in the header the moment they land.
 *
 * The two kinds of chip are two different actions — "stay here, change pair"
 * and "leave this venue" — so they are grouped rather than interleaved, and
 * the left edge of the strip is always the first kind.
 *
 * Renders nothing when this server has no stars. An empty bar would cost a row
 * of vertical space on every chart, permanently, to say "nothing here" — the
 * browser's star column is where the feature is discovered instead.
 */
export function FavoritesStrip({
  server,
  connector,
  pair,
  onPick,
}: FavoritesStripProps) {
  const { favorites, toggle, reorder } = useMarketFavorites(server);
  // Where the drag started, and the gap it would land in. Both are indices into
  // `favorites` — never into the rendered order, which now differs from it;
  // both are cleared by `dragend`, which fires on a cancelled drag as well as a
  // completed one.
  //
  // The source is a ref as well as state because `drop` has to read it, and a
  // handler only ever sees the state of the render that attached it — a drop
  // arriving before React has re-rendered since `dragstart` would read `null`
  // and quietly do nothing. The state copy exists solely to redraw the chips.
  const dragFromRef = useRef<number | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  if (favorites.length === 0) return null;

  const chips = group(favorites, connector);
  // Where the boundary falls, and where the dragged chip currently sits — both
  // in render positions, because both are about what the eye sees.
  const boundary = chips.findIndex(({ fav }) => fav.connector !== connector);
  const dragFromPos = chips.findIndex(({ index }) => index === dragFrom);

  // Whether two stars sit in the same half of the strip. The groups come from
  // the venue on screen, so a slot in the other group is a position the store
  // cannot hold — the chip would spring back the moment it re-grouped. Both the
  // keyboard and the mouse stop at that line, over indices into `favorites`.
  const sameGroup = (a: number, b: number) =>
    (favorites[a].connector === connector) ===
    (favorites[b].connector === connector);

  const endDrag = () => {
    dragFromRef.current = null;
    setDragFrom(null);
    setDragOver(null);
  };

  // Alt+←/→ is the same move without a mouse — the order is a preference, and
  // a preference that can only be expressed by dragging is one some people
  // cannot express at all. Alt, so the arrows keep scrolling the strip.
  const handleKeyDown = (e: React.KeyboardEvent, pos: number) => {
    if (!e.altKey) return;
    const step =
      e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : null;
    if (step === null) return;
    const from = chips[pos];
    const to = chips[pos + step];
    if (!to) return;
    if (!sameGroup(from.index, to.index)) return;
    e.preventDefault();
    reorder(from.index, to.index);
    // The chips are keyed by market, so the moved one keeps its DOM node and
    // the focus that is on it — it just arrives one slot over. Repeat-press
    // walks it down the strip, which is the whole point of the binding.
  };

  return (
    <div
      role="toolbar"
      aria-label="Starred markets"
      className="flex items-center gap-1 overflow-x-auto border-b border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 scrollbar-none"
    >
      <Star className="h-3 w-3 shrink-0 fill-[var(--color-yellow)] text-[var(--color-yellow)]" />
      {chips.map(({ fav: f, index: i }, pos) => {
        // The chart shows one venue at a time, so a chip is only "the one you
        // are on" when both halves match — otherwise clicking it moves venues.
        const active = f.pair === pair && f.connector === connector;
        const offVenue = f.connector !== connector;
        // The gap opens on the side the chip is travelling towards, so the
        // marker sits where the chip will actually land.
        const marking = dragOver === i && dragFrom !== null && dragFrom !== i;
        const edge = marking
          ? dragFromPos < pos
            ? "border-r-2 border-r-[var(--color-primary)]"
            : "border-l-2 border-l-[var(--color-primary)]"
          : "";
        return (
          <Fragment key={`${f.connector}:${f.pair}`}>
            {/* Where "change pair" stops and "change venue" starts. A hairline
                inside the existing row, so the boundary costs no height — and
                its own element rather than a border on the chip, which already
                spends its left border on the drop marker. */}
            {pos === boundary && pos > 0 && (
              <span
                role="separator"
                aria-orientation="vertical"
                className="mx-0.5 h-4 w-px shrink-0 bg-[var(--color-border)]"
              />
            )}
            <span
              draggable
              onDragStart={(e) => {
                dragFromRef.current = i;
                setDragFrom(i);
                e.dataTransfer.effectAllowed = "move";
                // Firefox starts no drag at all without payload on the transfer.
                e.dataTransfer.setData("text/plain", f.pair);
              }}
              onDragOver={(e) => {
                const from = dragFromRef.current;
                if (from === null) return;
                // Over the other group: leave the event undefaulted so the
                // browser shows no-drop, and clear the marker rather than let
                // the one on the last valid target linger, promising a landing
                // the store cannot hold.
                if (!sameGroup(from, i)) {
                  setDragOver(null);
                  return;
                }
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                setDragOver(i);
              }}
              onDrop={(e) => {
                e.preventDefault();
                const from = dragFromRef.current;
                if (from !== null && sameGroup(from, i)) reorder(from, i);
                endDrag();
              }}
              onDragEnd={endDrag}
              className={`group flex shrink-0 items-center rounded border text-[11px] transition-colors ${edge} ${
                dragFrom === i ? "opacity-40" : ""
              } ${
                active
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <button
                onClick={() => onPick({ connector: f.connector, pair: f.pair })}
                onKeyDown={(e) => handleKeyDown(e, pos)}
                aria-current={active ? "true" : undefined}
                title={
                  offVenue
                    ? `${f.pair} on ${formatConnectorName(f.connector)} — drag or Alt+←/→ to reorder`
                    : `${f.pair} — drag or Alt+←/→ to reorder`
                }
                className="cursor-grab py-0.5 pl-2 pr-1 font-medium active:cursor-grabbing"
              >
                {offVenue && (
                  <span className="mr-1 font-normal text-[var(--color-text-muted)]">
                    {formatConnectorName(f.connector)}
                  </span>
                )}
                {f.pair}
              </button>
              {/* Unstarring where the stars are: reaching the browser to undo a
                  chip you can see would be the same three actions again. */}
              <button
                onClick={() => toggle({ connector: f.connector, pair: f.pair })}
                aria-label={`Unstar ${f.pair} on ${formatConnectorName(f.connector)}`}
                title="Unstar"
                className="px-1 py-0.5 text-[var(--color-text-muted)] opacity-0 transition-opacity hover:text-[var(--color-text)] focus-visible:opacity-100 group-hover:opacity-100"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}
