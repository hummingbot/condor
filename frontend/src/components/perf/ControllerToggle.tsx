import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";

import { api } from "@/lib/api";

/**
 * Pause a quoting controller, or start a paused one — wherever it is listed.
 *
 * One component rather than a button per surface, because "is this thing
 * running" is not a question the app is allowed to answer two ways. The three
 * places a controller can be read from — the perf browser's report header, the
 * execution dock beside a conversation, and a strategy's deployed fleet on the
 * agent page — used to be one place you could act from and two you could only
 * look at, so the answer to "this one is misbehaving, stop it" depended on
 * which of them you happened to be reading. Now the control travels with the
 * row, and all three post to the same pair of endpoints and invalidate the same
 * query, so a controller paused from any of them is paused in all of them by
 * the next frame.
 *
 * **Paused is not stopped.** The endpoints flip `manual_kill_switch`: the
 * controller keeps its config, its history and whatever position it was left
 * holding, and the same click puts it back to quoting. That is why there is no
 * confirmation on it — an armed two-step belongs to stopping the *bot*, which
 * takes the container down and cannot be undone by clicking again.
 *
 * State is per instance, and per instance is per controller: the caller renders
 * one of these per row, so a pending or failed toggle stays on the row it
 * belongs to while the fleet refetches underneath it.
 */
export function ControllerToggle({
  server,
  bot,
  controllerId,
  stopped,
  stopping = false,
  label,
  variant = "icon",
}: {
  server: string;
  /** The bot that deployed it: one config on two bots is two controllers. */
  bot: string;
  controllerId: string;
  /** Its kill switch — *not* `status`, which the payload hardcodes. */
  stopped: boolean;
  /** The bot is on its way down, so the switch is not worth flipping. */
  stopping?: boolean;
  /** What the tooltip calls it; the config id when the caller says nothing. */
  label?: string;
  /**
   * `icon` for a table row, where the glyph is the whole control and the width
   * it costs is the width a column costs. `labelled` for a header with room to
   * spell the verb out.
   */
  variant?: "icon" | "labelled";
}) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () =>
      stopped
        ? api.startControllers(server, bot, [controllerId])
        : api.stopControllers(server, bot, [controllerId]),
    // The kill switch lives in the bots payload — every surface that draws this
    // control reads its state from there.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["bots", server] }),
  });

  const name = label || controllerId;
  const busy = toggle.isPending || stopping;
  const icon = variant === "icon" ? "h-3 w-3" : "h-3.5 w-3.5";

  return (
    <button
      type="button"
      data-controller-toggle
      disabled={busy}
      title={
        stopping
          ? "Stopping…"
          : toggle.isError
            ? `Could not ${stopped ? "start" : "pause"} ${name} — click to try again`
            : stopped
              ? `Start ${name} — it quotes again`
              : `Pause ${name} — it cancels its orders and stops quoting`
      }
      // The control is often inside something else that is clickable — a table
      // row that navigates, a fleet row that opens a scope — and a pause that
      // also left the page would look like it had done something else.
      onClick={(e) => {
        e.stopPropagation();
        toggle.mutate();
      }}
      onKeyDown={(e) => e.stopPropagation()}
      className={`inline-flex shrink-0 items-center justify-center rounded transition-colors disabled:opacity-50 ${
        variant === "icon" ? "h-4 w-4" : "gap-1.5 px-3 py-1.5 text-xs font-medium"
      } ${
        toggle.isError
          ? "text-[var(--color-red)]"
          : stopping
            ? "text-[var(--color-yellow)]"
            : stopped
              ? "text-[var(--color-green)] hover:bg-[var(--color-green)]/15"
              : "text-[var(--color-text-muted)] hover:bg-[var(--color-yellow)]/15 hover:text-[var(--color-yellow)]"
      }`}
    >
      {busy ? (
        <span
          className={`animate-spin rounded-full border-[1.5px] border-current border-t-transparent ${
            variant === "icon" ? "h-2.5 w-2.5" : "h-3.5 w-3.5"
          }`}
        />
      ) : stopped ? (
        <Play className={icon} />
      ) : (
        <Pause className={icon} />
      )}
      {variant === "labelled" && !busy && (stopped ? "Start" : "Pause")}
      {variant === "labelled" && busy && (stopping ? "Stopping…" : null)}
    </button>
  );
}
