import { KeyRound, ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";

export function ConnectKeysOverlay() {
  const navigate = useNavigate();

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center">
      {/* Frosted backdrop */}
      <div className="absolute inset-0 bg-[var(--color-bg)]/70 backdrop-blur-sm" />

      {/* Modal */}
      <div className="relative z-10 mx-4 w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 shadow-2xl shadow-black/20">
        <div className="flex flex-col items-center text-center">
          {/* Icon */}
          <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-[var(--color-primary)]/10 ring-1 ring-[var(--color-primary)]/20">
            <KeyRound className="h-8 w-8 text-[var(--color-primary)]" />
          </div>

          {/* Copy */}
          <h2 className="mb-2 text-xl font-bold text-[var(--color-text)]">
            Connect Your Exchange
          </h2>
          <p className="mb-6 max-w-xs text-sm leading-relaxed text-[var(--color-text-muted)]">
            Add your exchange API keys to unlock portfolio tracking, trading, and bot management.
          </p>

          {/* CTA */}
          <button
            onClick={() => navigate("/settings?tab=keys")}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-6 py-2.5 text-sm font-semibold text-white transition-all hover:bg-[var(--color-primary-hover)] hover:shadow-lg hover:shadow-[var(--color-primary)]/20 active:scale-[0.98]"
          >
            Connect API Keys
            <ArrowRight className="h-4 w-4" />
          </button>

          {/* Secondary hint */}
          <p className="mt-4 text-xs text-[var(--color-text-muted)]/60">
            You can also run routines without connecting keys
          </p>
        </div>
      </div>
    </div>
  );
}
