import { useEscapeKey } from "@/hooks/useEscapeKey";

// ── Discard Changes Confirm Dialog ──
// (Extracted from the retired hb editor dialogs; used by the Brain/Learnings
// editors in AgentDetail.)

export function DiscardChangesDialog({
  fileName,
  onDiscard,
  onClose,
}: {
  fileName: string;
  onDiscard: () => void;
  onClose: () => void;
}) {
  useEscapeKey(true, onClose);

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 p-5">
        <h2 className="text-sm font-semibold mb-2">Unsaved Changes</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">
          <span className="font-mono font-medium text-[var(--color-text)]">
            {fileName}
          </span>{" "}
          has unsaved changes. Closing will discard them.
        </p>
        <div className="flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]"
          >
            Keep editing
          </button>
          <button
            onClick={onDiscard}
            className="rounded-md bg-[var(--color-red)] px-4 py-1.5 text-sm font-medium text-white"
          >
            Discard changes
          </button>
        </div>
      </div>
    </>
  );
}
