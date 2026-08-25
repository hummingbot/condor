import { Check, CheckCircle, Copy, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { copyText } from "@/lib/clipboard";

interface ExecutorSuccessModalProps {
  executorId: string;
  title: string;
  subtitle?: string;
  /** Extra rows rendered inside the details box, after the Executor ID row. */
  details?: ReactNode;
  /** Label for the primary action button (default "Continue"). */
  primaryLabel?: string;
  /** Handler for the primary action button (defaults to onClose). */
  onPrimary?: () => void;
  onClose: () => void;
}

/**
 * Success dialog shown after creating an executor (order, LP position, grid…).
 * Shared by DexPool and CreateExecutor so the overlay, card styling and
 * copy-ID behavior stay consistent.
 */
export function ExecutorSuccessModal({
  executorId,
  title,
  subtitle,
  details,
  primaryLabel = "Continue",
  onPrimary,
  onClose,
}: ExecutorSuccessModalProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="relative w-[360px] rounded-xl border border-[var(--color-green)]/30 bg-[var(--color-surface)] p-6 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute right-3 top-3 rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          <X className="h-4 w-4" />
        </button>

        {/* Icon */}
        <div className="mb-4 flex justify-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-green)]/15">
            <CheckCircle className="h-6 w-6 text-[var(--color-green)]" />
          </div>
        </div>

        {/* Title */}
        <h3 className="mb-1 text-center text-sm font-semibold text-[var(--color-text)]">
          {title}
        </h3>
        {subtitle && (
          <p className="mb-4 text-center text-[11px] text-[var(--color-text-muted)]">
            {subtitle}
          </p>
        )}

        {/* Details */}
        <div className="mb-4 space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-[var(--color-text-muted)]">Executor ID</span>
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[var(--color-text)]">
                {executorId.slice(0, 12)}…
              </span>
              <button
                onClick={() => {
                  void copyText(executorId).then((ok) => setCopied(ok));
                }}
                className="rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Copy full ID"
              >
                {copied ? (
                  <Check className="h-3 w-3 text-[var(--color-green)]" />
                ) : (
                  <Copy className="h-3 w-3" />
                )}
              </button>
            </div>
          </div>
          {details}
        </div>

        {/* Action */}
        <button
          onClick={onPrimary ?? onClose}
          className="w-full rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:brightness-110"
        >
          {primaryLabel}
        </button>
      </div>
    </div>
  );
}

/**
 * Bottom-right error toast for executor create mutations. Rendered inside a
 * relatively-positioned page container.
 */
export function ErrorToast({ message }: { message: string }) {
  return (
    <div className="absolute bottom-16 right-4 rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-4 py-2 text-sm text-[var(--color-red)]">
      {message}
    </div>
  );
}
