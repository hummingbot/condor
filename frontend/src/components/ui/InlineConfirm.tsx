import { Check, Loader2, Trash2, X } from "lucide-react";

/**
 * The two-click delete affordance for a row or a toolbar: a trash trigger that
 * swaps itself for a confirm/cancel pair.
 *
 * It was written by hand six times over (credentials, Gateway wallets, servers,
 * LLM endpoints and the two report headers) and had drifted in every dimension
 * — Check/X here, "Yes"/"No" or "Forget" there, a pending spinner in one copy
 * and none in the rest. A modal (`components/agent/ConfirmDialog`) is the wrong
 * control inside a table row, which is why they were hand-rolled; this is that
 * control, once.
 *
 * The caller keeps owning the `confirming` state, so a list can hold the id of
 * the single row awaiting confirmation rather than one boolean per row.
 */
export interface InlineConfirmProps {
  /** True while this control is the one awaiting a confirming click. */
  confirming: boolean;
  onRequest: () => void;
  onConfirm: () => void;
  onCancel: () => void;
  /** Runs a spinner in the confirm button and blocks a second submission. */
  pending?: boolean;
  /** Title and accessible name of the trash trigger, e.g. "Delete credential". */
  triggerLabel: string;
  /** Blocks the first click (owner-only rows); `triggerLabel` should say why. */
  disabled?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Icon scale: `sm` for dense settings rows, `md` for report toolbars. */
  size?: "sm" | "md";
}

export function InlineConfirm({
  confirming,
  onRequest,
  onConfirm,
  onCancel,
  pending = false,
  triggerLabel,
  disabled = false,
  confirmLabel = "Confirm delete",
  cancelLabel = "Cancel delete",
  size = "sm",
}: InlineConfirmProps) {
  const icon = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";

  if (!confirming) {
    return (
      <button
        onClick={onRequest}
        disabled={disabled}
        title={triggerLabel}
        aria-label={triggerLabel}
        className="shrink-0 rounded p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-muted)]"
      >
        <Trash2 className={icon} />
      </button>
    );
  }

  return (
    <div className="flex shrink-0 items-center gap-1">
      <button
        onClick={onConfirm}
        disabled={pending}
        title={confirmLabel}
        aria-label={confirmLabel}
        className="rounded p-1.5 text-[var(--color-red)] hover:bg-[var(--color-red)]/10 disabled:opacity-50"
      >
        {pending ? (
          <Loader2 className={`${icon} animate-spin`} />
        ) : (
          <Check className={icon} />
        )}
      </button>
      <button
        onClick={onCancel}
        title={cancelLabel}
        aria-label={cancelLabel}
        className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
      >
        <X className={icon} />
      </button>
    </div>
  );
}
