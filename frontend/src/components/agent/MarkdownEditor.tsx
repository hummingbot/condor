import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useCallback, useState } from "react";

export function MarkdownEditor({
  label,
  sublabel,
  content,
  onSave,
  invalidateKey,
  onDirtyChange,
  minHeightClass = "min-h-[500px]",
  showLabel = true,
}: {
  label: string;
  sublabel: string;
  content: string;
  onSave: (value: string) => Promise<unknown>;
  invalidateKey: unknown[];
  /** Notifies the host (e.g. a closable modal) when there are unsaved edits. */
  onDirtyChange?: (dirty: boolean) => void;
  /**
   * How tall the box starts. A near-full-screen modal can afford 500px; a card
   * sharing a row with another card inside a disclosure cannot, and a fixed
   * height there is what turns two documents into a page of scrollbars.
   */
  minHeightClass?: string;
  /**
   * Whether the box names itself. Off for a host that already has a titled
   * header of its own — a card whose chrome says "Playbook · strategy.md"
   * printing it again a row below is the same words twice in two sizes.
   */
  showLabel?: boolean;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => onSave(value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invalidateKey });
      setDirty(false);
      onDirtyChange?.(false);
    },
  });

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    setDirty(true);
    onDirtyChange?.(true);
  }, [onDirtyChange]);

  return (
    <div className="flex flex-col gap-2">
      <div className={`flex items-center ${showLabel ? "justify-between" : "justify-end"}`}>
        {showLabel ? (
          <div>
            <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{label}</span>
            <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">{sublabel}</span>
          </div>
        ) : (
          <span className="sr-only">{label}</span>
        )}
        <button
          onClick={() => saveMut.mutate()}
          disabled={!dirty || saveMut.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          <Save className="h-3.5 w-3.5" />
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {saveMut.isError && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {saveMut.error instanceof Error ? saveMut.error.message : "Save failed"}
        </div>
      )}
      <textarea
        value={value}
        onChange={handleChange}
        spellCheck={false}
        className={`${minHeightClass} w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50`}
      />
    </div>
  );
}
