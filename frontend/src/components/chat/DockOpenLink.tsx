import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

/**
 * The foot of a rail panel: the door to the full page it previews.
 *
 * The rail's panels are glances at something that has its own home — Portfolio,
 * Activity, Routines, every loop — and each says where that home is, in the
 * same words and the same place, so a reader who wants more than the glance
 * never has to know the page exists to find it.
 */
export function DockOpenLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      {label}
      <ArrowRight className="h-3 w-3" />
    </Link>
  );
}
