// Single source of truth for the experiment execution-mode color
// (dry_run = blue, run_once = amber), as read by the Lab's run rail.
export const MODE_STYLES: Record<string, { text: string }> = {
  dry_run: { text: "text-blue-400" },
  run_once: { text: "text-amber-400" },
};
