// Single source of truth for the experiment execution-mode encoding
// (dry_run = blue, run_once = amber). `badge` styles the ModeBadge pill;
// `text` is shared with compact renderings (e.g. SessionReviewer's sidebar)
// that only need the mode color.
export const MODE_STYLES: Record<string, { label: string; badge: string; text: string }> = {
  dry_run: { label: "DRY RUN", badge: "border-blue-500/30 bg-blue-500/10 text-blue-400", text: "text-blue-400" },
  run_once: { label: "RUN ONCE", badge: "border-amber-500/30 bg-amber-500/10 text-amber-400", text: "text-amber-400" },
};
