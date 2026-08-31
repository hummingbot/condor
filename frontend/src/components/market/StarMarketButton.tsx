import { Star } from "lucide-react";

import { useMarketFavorites } from "@/lib/marketFavorites";

interface StarMarketButtonProps {
  server: string;
  connector: string;
  pair: string;
}

/**
 * Star the market the trade surface is currently on.
 *
 * The star column in the market browser only reaches a pair you went looking
 * for. The one you are already watching — the whole reason you would want it on
 * the strip — could only be starred by opening the overlay and finding the row
 * you had just left it on. This is that same toggle, on the pair in the header.
 */
export function StarMarketButton({
  server,
  connector,
  pair,
}: StarMarketButtonProps) {
  const { toggle, isFavorite } = useMarketFavorites(server);

  if (!pair) return null;

  const starred = isFavorite({ connector, pair });

  return (
    <button
      onClick={() => toggle({ connector, pair })}
      aria-pressed={starred}
      aria-label={`${starred ? "Unstar" : "Star"} ${pair}`}
      title={starred ? "Remove from favourites" : "Add to favourites"}
      className={`flex items-center py-2.5 pl-3 pr-1 transition-colors hover:bg-[var(--color-surface-hover)] ${
        starred
          ? "text-[var(--color-yellow)]"
          : "text-[var(--color-text-muted)]/50 hover:text-[var(--color-text-muted)]"
      }`}
    >
      <Star className={`h-3.5 w-3.5 ${starred ? "fill-current" : ""}`} />
    </button>
  );
}
