import type { ReactNode } from "react";

import { useEscapeKey } from "@/hooks/useEscapeKey";

/**
 * Shared destructive-confirmation modal for agent/strategy delete flows.
 *
 * Renders the overlay + card + Cancel + destructive button shell that was
 * previously copy-pasted across Agents.tsx and AgentDetail.tsx (ARCH-059).
 * The destructive button uses the themed `bg-[var(--color-red)]` token so it
 * honors the colorblind theme's red remap (index.css), matching the editor
 * dialogs (EditorDialogs.tsx) instead of a hardcoded `bg-red-500`.
 *
 * Closes on Escape via `useEscapeKey` (CORR-077) while `open` is true.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = "Delete",
  pendingLabel = "Deleting...",
  isPending = false,
  isError = false,
  errorText,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  pendingLabel?: string;
  isPending?: boolean;
  isError?: boolean;
  errorText?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useEscapeKey(open, onClose);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">{title}</h2>
        <div className="mb-6 text-sm text-[var(--color-text-muted)]">{children}</div>
        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isPending}
            className="rounded-lg bg-[var(--color-red)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {isPending ? pendingLabel : confirmLabel}
          </button>
        </div>
        {isError && errorText && (
          <p className="mt-3 text-xs text-[var(--color-red)]">{errorText}</p>
        )}
      </div>
    </div>
  );
}
